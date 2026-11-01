"""The Apify Instagram reel scraper's dataset items, as /viral reads them.
The actor is third-party and its key names drift: the mapping lives here."""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApifyReel(BaseModel):
    """One reel of the profile."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    code: str = Field(alias="shortCode")
    url: str
    # Usually the progressive mp4 with sound; for a reel Instagram keeps as
    # separate streams only, the picture, its sound in `audio_url`.
    video_url: str | None = Field(default=None, alias="videoUrl")
    audio_url: str | None = Field(default=None, alias="audioUrl")
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


class ApifyItemError(BaseModel):
    """An item the actor stores instead of reels: `not_found` (no such
    profile) or `no_items` ("Empty or private data for provided input")."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    error: str
    description: str = Field(default="", alias="errorDescription")


class SavedReel(BaseModel):
    reel: ApifyReel
    path: Path


class ViralFind(BaseModel):
    """A search's outcome: the top reels saved, most played first; how many
    reels were read and what the run cost."""

    reels: list[SavedReel]
    scanned: int
    cost_usd: float
