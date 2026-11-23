# Copyright (C) 2026 jpkrajewski

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

TWEET = "tweet"
MOCK_TWEET = "mock_tweet"


class _Item(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class TweetSearchInput(_Item):
    author: str = Field(serialization_alias="from")
    since_time: str
    query_type: str = Field(default="Latest", serialization_alias="queryType")
    max_items: int = Field(serialization_alias="maxItems")


class ApifyTweetAuthor(_Item):
    user_name: str = Field(alias="userName")
    id: str = ""
    name: str = ""
    followers: int
    following: int | None = None
    profile_picture: str = Field(default="", alias="profilePicture")
    is_blue_verified: bool = Field(default=False, alias="isBlueVerified")
    protected: bool | None = None

    @field_validator("id", mode="before")
    @classmethod
    def _as_text(cls, value: object) -> object:
        return "" if value is None else str(value)


class _Media(_Item):
    media_url_https: str = ""


class _Entities(_Item):
    media: list[_Media] = []


class ApifyTweet(_Item):
    type: str = ""
    id: str
    url: str = ""
    created_at: datetime | None = Field(default=None, alias="createdAt")
    views: int | None = Field(default=None, alias="viewCount")
    retweeted: dict[str, object] | None = Field(default=None, alias="retweeted_tweet")
    author: ApifyTweetAuthor | None = None
    entities: _Entities | None = Field(default=None, alias="extendedEntities")

    @field_validator("id", mode="before")
    @classmethod
    def _as_text(cls, value: object) -> object:
        return str(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _twitter_time(cls, value: object) -> object:
        if isinstance(value, str):
            return datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y")
        return value

    @property
    def own_post(self) -> bool:
        return (
            self.type == TWEET
            and self.id.isdigit()
            and self.created_at is not None
            and self.author is not None
            and self.retweeted is None
        )

    @property
    def posted_at(self) -> float:
        return self.created_at.timestamp() if self.created_at else 0.0

    @property
    def thumbnail_url(self) -> str:
        media = self.entities.media if self.entities else []
        return media[0].media_url_https if media else ""
