# Copyright (C) 2026 jpkrajewski

import re
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class SlugRules(BaseModel):
    min_length: int = Field(ge=1)
    max_length: int = Field(ge=1)
    invalid: str
    unique_per: Literal["provider", "domain"]

    def make(self, text: str) -> str:
        slug = re.sub(self.invalid, "-", text.lower())
        slug = re.sub(r"[^a-z0-9]+$", "", re.sub(r"^[^a-z0-9]+", "", slug))
        slug = slug[: self.max_length].rstrip("-._")
        if len(slug) < self.min_length:
            slug = f"{slug}-link".lstrip("-")
        return slug

    def numbered(self, slug: str, n: int) -> str:
        suffix = f"-{n}"
        return slug[: self.max_length - len(suffix)].rstrip("-._") + suffix


class ProviderInfo(BaseModel):
    key: str = ""
    label: str
    base_url: str
    docs_url: str
    key_prefix: str
    domains: list[str] = Field(default_factory=list)
    slug: SlugRules
    min_interval_s: float = Field(ge=0)
    stats_batch: int = Field(ge=1)
    max_history_days: int = Field(ge=1)
    max_span_days: int = Field(ge=1)
    behavior: str | None = None

    @property
    def default_domain(self) -> str:
        return self.domains[0] if self.domains else ""


class Target(BaseModel):
    key: str = ""
    label: str
    hosts: list[str] = Field(min_length=1)
    prefix: str
    profile: str

    def matches(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return any(host == h or host.endswith(f".{h}") for h in self.hosts)

    def problem(self, url: str) -> str | None:
        if not self.matches(url):
            return f"Not a {self.label} link: {url}"
        if not re.fullmatch(self.profile, urlsplit(url).path):
            return f"Add the {self.label} profile after {self.prefix}"
        return None


class NewLink(BaseModel):
    slug: str
    domain: str
    destination: str
    title: str = ""


class RemoteLink(BaseModel):
    remote_id: str
    slug: str
    domain: str
    url: str
    destination: str
    active: bool
    adult: bool | None = None
    created_at: float | None = None

    @field_validator("url", "destination")
    @classmethod
    def _http_only(cls, value: str) -> str:
        if value and urlsplit(value).scheme not in {"http", "https"}:
            msg = "must be an http(s) URL"
            raise ValueError(msg)
        return value


class DayStat(BaseModel):
    day: date
    views: int = Field(ge=0)
    clicks: int = Field(ge=0)


class HourStat(BaseModel):
    hour: datetime
    views: int = Field(ge=0)
    clicks: int = Field(ge=0)


class LinkDays(BaseModel):
    remote_id: str
    days: list[DayStat]


class LinkHours(BaseModel):
    remote_id: str
    hours: list[HourStat]


class Usage(BaseModel):
    used: int
    limit: int | None
    active: int | None = None
    active_limit: int | None = None

    @property
    def full(self) -> bool:
        return self.limit is not None and self.used >= self.limit

    @property
    def room(self) -> int | None:
        free = [
            limit - used
            for limit, used in (
                (self.limit, self.used),
                (self.active_limit, self.active),
            )
            if limit is not None and used is not None
        ]
        return max(min(free), 0) if free else None
