# Copyright (C) 2026 jpkrajewski

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class PostKind(StrEnum):
    image = "image"
    video = "video"


class PostSource(StrEnum):
    posted = "posted"
    threads = "threads"


class PostStatus(StrEnum):
    queued = "queued"
    publishing = "publishing"
    published = "published"
    failed = "failed"


class ModelPreset(BaseModel, frozen=True):
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
        too_long = [d for d in self.descriptions if len(d) > 500]  # noqa: PLR2004
        if too_long:
            msg = f"{self.key}: {len(too_long)} description(s) over 500 characters"
            raise ValueError(msg)
        return self


class ModelLink(BaseModel):
    preset: str
    link_id: int
    url: str


class Channel(BaseModel):
    id: int
    user_id: int
    platform: str
    account_name: str
    paused: bool = False
    error: str | None = None
    next_at: float | None = None
    resume_at: float | None = None
    cookies_expire_at: float | None = None


class Failing(BaseModel):
    channel_id: int
    error: str


class NewPost(BaseModel):
    file_id: str
    name: str
    kind: PostKind
    source: PostSource
    preset: str


class Post(NewPost):
    id: int
    channel_id: int
    status: PostStatus
    attempts: int = 0
    job_id: str | None = None
    remote_id: str | None = None
    title: str | None = None
    description: str | None = None
    link: str | None = None
    error: str | None = None
    created_at: float
    started_at: float | None = None
    published_at: float | None = None


class PostCounts(BaseModel):
    queued: int = 0
    publishing: int = 0
    published: int = 0
    failed: int = 0
    today: int = 0


class PinDay(BaseModel):
    channel_id: int
    preset: str
    day: date
    published: int = 0
    ig: int = 0
    failed: int = 0
