# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, ConfigDict, Field

SNOWFLAKE_EPOCH_MS = 1288834974657
_SNOWFLAKE_TIME_SHIFT = 22


class _Page(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class _Counts(_Page):
    followers: int
    following: int | None = None


class _Core(_Page):
    screen_name: str
    name: str = ""


class _Privacy(_Page):
    protected: bool = False


class _Avatar(_Page):
    image_url: str = ""


class _Verification(_Page):
    is_blue_verified: bool = False


class _Views(_Page):
    count: int = 0


class _Details(_Page):
    created_at_ms: int = 0


class _Media(_Page):
    media_url_https: str = ""


class PostResult(_Page):
    rest_id: str = ""
    details: _Details = Field(default_factory=_Details)
    views: _Views = Field(default_factory=_Views)
    media: list[_Media] = Field(default=[], alias="media_entities2")
    tweet: PostResult | None = None

    @property
    def own(self) -> PostResult:
        return self.tweet or self

    @property
    def posted_ms(self) -> int:
        if self.details.created_at_ms:
            return self.details.created_at_ms
        return (int(self.rest_id) >> _SNOWFLAKE_TIME_SHIFT) + SNOWFLAKE_EPOCH_MS


class _PostResults(_Page):
    result: PostResult | None = None


class _ItemContent(_Page):
    tweet_results: _PostResults | None = None


class _EntryContent(_Page):
    content: _ItemContent | None = None


class _Entry(_Page):
    content: _EntryContent


class _Instruction(_Page):
    entries: list[_Entry] = []


class _Timeline(_Page):
    instructions: list[_Instruction] = []


class _TimelineHolder(_Page):
    timeline: _Timeline


class UserResult(_Page):
    rest_id: str = ""
    core: _Core | None = None
    counts: _Counts | None = Field(default=None, alias="relationship_counts")
    privacy: _Privacy = Field(default_factory=_Privacy)
    avatar: _Avatar = Field(default_factory=_Avatar)
    verification: _Verification = Field(default_factory=_Verification)
    originals: _TimelineHolder | None = Field(
        default=None,
        alias="profile_user_originals_timeline",
    )

    def posts(self) -> list[PostResult]:
        if self.originals is None:
            return []
        return [
            entry.content.content.tweet_results.result.own
            for step in self.originals.timeline.instructions
            for entry in step.entries
            if entry.content.content
            and entry.content.content.tweet_results
            and entry.content.content.tweet_results.result
            and entry.content.content.tweet_results.result.own.rest_id.isdigit()
        ]


class _ByName(_Page):
    result: UserResult | None = None


class _Data(_Page):
    user: _ByName | None = Field(default=None, alias="user_result_by_screen_name")


class Answer(_Page):
    data: _Data = Field(default_factory=_Data)


class XPost(BaseModel):
    id: str
    url: str
    views: int
    posted_at: float
    thumbnail_url: str = ""


class XProfile(BaseModel):
    username: str
    user_id: str
    name: str = ""
    followers: int
    following: int | None = None
    is_protected: bool = False
    is_verified: bool = False
    avatar_url: str = ""
    posts: list[XPost] = []
    complete: bool
