"""/distribution's shapes: an account on another site (a Pinterest account)
posting one model's content, and the files queued for it. Stored by
`db/repositories/distribution.py`, shown by the page, run by the sweep."""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class PostKind(StrEnum):
    image = "image"
    video = "video"


class PostSource(StrEnum):
    """Which of an account's Drive folders the file came from."""

    posted = "posted"
    threads = "threads"


class PostStatus(StrEnum):
    queued = "queued"
    publishing = "publishing"
    published = "published"
    failed = "failed"


class ModelPreset(BaseModel, frozen=True):
    """One model's preset (`distribution_presets`, private to its owner;
    how to write one: features/distribution/CLAUDE.md "Presets"): what every
    Pinterest account of the model posts with. `creator` = the /accounts
    creator whose Instagram accounts' POSTED + THREADS are its content;
    `title`, `board`, `descriptions` (one drawn per pin); its self links
    (`slug`-n on `domain` to `destination`, a /links `target`); `instagram`:
    the handles an "ig: <handle>" pin names (no caption, no link)."""

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    creator: str
    title: str = Field(max_length=100)
    board: str = Field(max_length=50)
    target: str
    destination: HttpUrl
    domain: str
    slug: str
    instagram: list[str] = Field(default_factory=list)
    descriptions: list[str] = Field(min_length=1)

    def checked(self) -> ModelPreset:
        """Pinterest takes 500 characters of a description from us."""
        too_long = [d for d in self.descriptions if len(d) > 500]  # noqa: PLR2004 -- Pinterest's limit
        if too_long:
            msg = f"{self.key}: {len(too_long)} description(s) over 500 characters"
            raise ValueError(msg)
        return self


class ModelLink(BaseModel):
    """A model's one self link (all its pins carry it)."""

    preset: str
    link_id: int
    url: str


class Channel(BaseModel):
    """One account on one site (`platform`). Belongs to no model: the
    sweep spreads every model's content over all of its owner's accounts.
    Without its cookies: those only ever leave the database for a call."""

    id: int
    user_id: int
    platform: str
    account_name: str
    paused: bool = False
    # Why it stopped (dead cookies): shown on the page, cleared by new ones.
    error: str | None = None
    next_at: float | None = None
    resume_at: float | None = None
    # When its Pinterest login cookie expires (epoch s; None: unknown).
    cookies_expire_at: float | None = None


class Failing(BaseModel):
    """An account whose pins only failed lately, and the last reason."""

    channel_id: int
    error: str


class NewPost(BaseModel):
    """A Drive file found for a channel's queue."""

    file_id: str
    name: str
    kind: PostKind
    source: PostSource
    # The model it is of (its preset key).
    preset: str


class Post(NewPost):
    id: int
    channel_id: int
    status: PostStatus
    attempts: int = 0
    job_id: str | None = None
    # The pin's id on the site, once made.
    remote_id: str | None = None
    # What it was pinned with, drawn when claimed: the model's title, a
    # caption and the account's link, or "ig: <handle>" alone (no caption,
    # no link).
    title: str | None = None
    description: str | None = None
    link: str | None = None
    error: str | None = None
    created_at: float
    started_at: float | None = None
    published_at: float | None = None


class PostCounts(BaseModel):
    """A channel's queue in numbers."""

    queued: int = 0
    publishing: int = 0
    published: int = 0
    failed: int = 0
    # Published since the start of today (UTC).
    today: int = 0


class PinDay(BaseModel):
    """One channel's pins on one UTC day: made (of them, `ig` = the "ig:
    <handle>" kind, no link), and failed tries."""

    channel_id: int
    preset: str
    day: date
    published: int = 0
    ig: int = 0
    failed: int = 0
