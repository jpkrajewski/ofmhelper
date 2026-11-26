# Copyright (C) 2026 jpkrajewski

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Item(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class ProfileScraperInput(_Item):
    usernames: list[str]
    include_about_section: bool = Field(
        default=False,
        serialization_alias="includeAboutSection",
    )


class ReelScraperInput(_Item):
    username: list[str]
    results_limit: int = Field(serialization_alias="resultsLimit")
    only_posts_newer_than: str | None = Field(
        default=None,
        serialization_alias="onlyPostsNewerThan",
    )
    skip_pinned_posts: bool = Field(
        default=False,
        serialization_alias="skipPinnedPosts",
    )
    include_shares_count: bool = Field(
        default=False,
        serialization_alias="includeSharesCount",
    )
    include_transcript: bool = Field(
        default=False,
        serialization_alias="includeTranscript",
    )
    include_downloaded_video: bool = Field(
        default=False,
        serialization_alias="includeDownloadedVideo",
    )


class ApifyReel(_Item):
    code: str = Field(alias="shortCode", pattern=r"^[A-Za-z0-9_-]{5,64}$")
    url: str
    owner: str = Field(default="", alias="ownerUsername")
    video_url: str | None = Field(default=None, alias="videoUrl")
    audio_url: str | None = Field(default=None, alias="audioUrl")
    thumbnail_url: str = Field(default="", alias="displayUrl")
    play_count: int | None = Field(default=None, alias="videoPlayCount")
    view_count: int | None = Field(default=None, alias="videoViewCount")
    likes: int | None = Field(default=None, alias="likesCount")
    comments: int | None = Field(default=None, alias="commentsCount")
    posted_at: datetime | None = Field(default=None, alias="timestamp")
    seconds: float | None = Field(default=None, alias="videoDuration")

    @field_validator("likes", "comments", "play_count", "view_count", mode="after")
    @classmethod
    def _hidden_is_unknown(cls, value: int | None) -> int | None:
        return value if value is None or value >= 0 else None

    @property
    def plays(self) -> int:
        return self.play_count or self.view_count or 0


class ApifyProfile(_Item):
    username: str
    id: str = ""
    full_name: str = Field(default="", alias="fullName")
    followers: int = Field(alias="followersCount")
    following: int | None = Field(default=None, alias="followsCount")
    is_private: bool = Field(default=False, alias="private")
    is_verified: bool = Field(default=False, alias="verified")
    avatar_url: str = Field(default="", alias="profilePicUrlHD")
    small_avatar_url: str = Field(default="", alias="profilePicUrl")
    input_url: str = Field(default="", alias="inputUrl")


class ApifyItemError(_Item):
    error: str
    description: str = Field(default="", alias="errorDescription")
