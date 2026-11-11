"""Apify's Instagram actors' dataset items (`apify~instagram-reel-scraper`,
`apify~instagram-profile-scraper`). The actors are third-party and their key
names drift: the mapping lives here, for every caller (/viral, the /accounts
Apify source)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Item(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class ProfileScraperInput(_Item):
    """The profile scraper's input, sent under the actor's names."""

    usernames: list[str]
    include_about_section: bool = Field(
        default=False, serialization_alias="includeAboutSection"
    )


class ReelScraperInput(_Item):
    """The reel scraper's input, sent under the actor's names: each
    account's newest `results_limit` reels (always set: without it the actor
    reads the whole profile), every paid add-on off."""

    username: list[str]
    results_limit: int = Field(serialization_alias="resultsLimit")
    # "N days"; None = not sent, no age filter.
    only_posts_newer_than: str | None = Field(
        default=None, serialization_alias="onlyPostsNewerThan"
    )
    skip_pinned_posts: bool = Field(
        default=False, serialization_alias="skipPinnedPosts"
    )
    include_shares_count: bool = Field(
        default=False, serialization_alias="includeSharesCount"
    )
    include_transcript: bool = Field(
        default=False, serialization_alias="includeTranscript"
    )
    include_downloaded_video: bool = Field(
        default=False, serialization_alias="includeDownloadedVideo"
    )


class ApifyReel(_Item):
    """One reel of a profile (the reel scraper)."""

    code: str = Field(alias="shortCode")
    url: str
    owner: str = Field(default="", alias="ownerUsername")
    # Usually the progressive mp4 with sound; for a reel Instagram keeps as
    # separate streams only, the picture, its sound in `audio_url`.
    video_url: str | None = Field(default=None, alias="videoUrl")
    audio_url: str | None = Field(default=None, alias="audioUrl")
    thumbnail_url: str = Field(default="", alias="displayUrl")
    # Plays: what Instagram shows. Views: deprecated by Instagram, a fallback.
    play_count: int | None = Field(default=None, alias="videoPlayCount")
    view_count: int | None = Field(default=None, alias="videoViewCount")
    likes: int | None = Field(default=None, alias="likesCount")
    comments: int | None = Field(default=None, alias="commentsCount")
    posted_at: datetime | None = Field(default=None, alias="timestamp")
    seconds: float | None = Field(default=None, alias="videoDuration")

    @field_validator("likes", "comments", "play_count", "view_count", mode="after")
    @classmethod
    def _hidden_is_unknown(cls, value: int | None) -> int | None:
        """Instagram answers -1 for a count the owner hid."""
        return value if value is None or value >= 0 else None

    @property
    def plays(self) -> int:
        """What Instagram shows on the reel: plays, else (older reels) views."""
        return self.play_count or self.view_count or 0


class ApifyProfile(_Item):
    """One profile (the profile scraper). `input_url`: the profile asked for,
    which still names the old handle of a renamed account."""

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
    """An item an actor stores instead of results: `not_found` (no such
    profile) or `no_items` ("Empty or private data for provided input")."""

    error: str
    description: str = Field(default="", alias="errorDescription")
