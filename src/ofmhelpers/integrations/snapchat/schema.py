from pydantic import BaseModel, ConfigDict, Field


class _Page(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class _Value(_Page):
    value: str = ""


class PublicProfile(_Page):
    username: str
    title: str = ""
    subscribers: int = Field(alias="subscriberCount")
    avatar_url: str = Field(default="", alias="profilePictureUrl")
    user_id: str = Field(default="", alias="hostUserId")


class UserProfile(_Page):
    case: str = Field(default="", alias="$case")
    public: PublicProfile | None = Field(default=None, alias="publicProfileInfo")


class Highlight(_Page):
    story_id: _Value | None = Field(default=None, alias="storyId")


class _Video(_Page):
    uploaded_ms: int = Field(default=0, alias="uploadDateMs")
    views: int = Field(default=0, alias="viewCount")
    thumbnail_url: str = Field(default="", alias="thumbnailUrl")


class _Engagement(_Page):
    views: int = Field(default=0, alias="viewCount")


class SpotlightMetadata(_Page):
    video: _Video = Field(default_factory=_Video, alias="videoMetadata")
    engagement: _Engagement | None = Field(default=None, alias="engagementStats")

    @property
    def views(self) -> int:
        return self.engagement.views if self.engagement else self.video.views


class ProfileProps(_Page):
    user: UserProfile = Field(default_factory=UserProfile, alias="userProfile")
    highlights: list[Highlight] = Field(default=[], alias="spotlightHighlights")
    spotlights: list[SpotlightMetadata] = Field(
        default=[], alias="spotlightStoryMetadata"
    )
    cursor: str = Field(default="", alias="spotlightHighlightsCursor")


class _Props(_Page):
    page: ProfileProps = Field(alias="pageProps")


class NextData(_Page):
    props: _Props


class SpotlightPost(BaseModel):
    id: str
    url: str
    views: int
    posted_at: float
    thumbnail_url: str = ""


class SnapchatProfile(BaseModel):
    username: str
    user_id: str
    display_name: str = ""
    subscribers: int | None
    avatar_url: str = ""
    posts: list[SpotlightPost] = []
    hidden_posts: int = 0
    complete: bool
