# Copyright (C) 2026 jpkrajewski

import re
from statistics import median
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ValidationError,
    computed_field,
    field_validator,
)

_USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")


_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


CDN_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")


GLOBAL_CDN_HOST = "scontent.cdninstagram.com"


def _global_cdn(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(
        CDN_HOST_SUFFIXES,
    ):
        return value
    return parts._replace(netloc=GLOBAL_CDN_HOST).geturl()


MediaUrl = Annotated[str, AfterValidator(_global_cdn)]


class ProfileHandle(BaseModel):
    username: str

    @field_validator("username", mode="before")
    @classmethod
    def _bare(cls, value: str) -> str:
        text = str(value).strip()
        if "instagram.com" in text:
            has_scheme = text.lower().startswith(("http://", "https://", "//"))
            parts = urlsplit(text if has_scheme else f"https://{text}")
            host = (parts.hostname or "").lower().rstrip(".")
            if host != "instagram.com" and not host.endswith(".instagram.com"):
                msg = f"Not an Instagram username: {value!r}"
                raise ValueError(msg)
            text = next((part for part in parts.path.split("/") if part), "")
        text = text.lstrip("@").strip("/")
        if not _USERNAME.fullmatch(text):
            msg = f"Not an Instagram username: {value!r}"
            raise ValueError(msg)
        return text.lower()


class Profile(BaseModel):
    user_id: str
    username: str
    followers: int
    is_private: bool
    full_name: str = ""
    following: int | None = None
    is_verified: bool = False
    avatar_url: MediaUrl = ""


VIEW_TIERS = (
    (1_000_000, "legendary"),
    (500_000, "epic"),
    (200_000, "rare"),
    (100_000, "uncommon"),
    (50_000, "common"),
)


class Reel(BaseModel):
    code: str
    taken_at: int
    duration_s: float | None
    views: int
    followers: int
    thumbnail_url: MediaUrl
    video_url: MediaUrl | None

    @property
    def pk(self) -> str:
        pk = 0
        for char in self.code:
            pk = pk * 64 + _SHORTCODE_ALPHABET.index(char)
        return str(pk)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ratio(self) -> float:
        return self.views / max(self.followers, 1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tier(self) -> str | None:
        return next((name for floor, name in VIEW_TIERS if self.views >= floor), None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        return f"https://www.instagram.com/reel/{self.code}/"


class ReelStat(BaseModel):
    code: str
    views: int
    posted_at: float


class ProfileReels(BaseModel):
    profile: Profile
    scanned: int
    reels: list[Reel]
    window_days: float = 0
    recent: list[ReelStat] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def views_total(self) -> int:
        return sum(r.views for r in self.recent)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def views_max(self) -> int:
        return max((r.views for r in self.recent), default=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def views_median(self) -> int:
        return int(median(r.views for r in self.recent)) if self.recent else 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def eligible(self) -> int:
        return len(self.reels)


class WatchedProfile(BaseModel):
    user_id: int
    username: str
    group: str | None = None
    added_at: float
    scanned_at: float | None = None
    error: str | None = None
    result: ProfileReels | None = None

    @property
    def pending(self) -> bool:
        return self.scanned_at is None

    @property
    def views_total(self) -> int:
        return self.result.views_total if self.result else 0

    @classmethod
    def most_viewed(cls, profiles: list[WatchedProfile]) -> str | None:
        top = max(profiles, key=lambda p: p.views_total, default=None)
        return top.username if top and top.views_total else None

    @classmethod
    def most_reels(
        cls,
        profiles: list[WatchedProfile],
        excluding: str | None,
    ) -> str | None:
        rest = [p for p in profiles if p.username != excluding]
        top = max(rest, key=lambda p: (p.reel_count, p.views_total), default=None)
        return top.username if top and top.reel_count else None

    @property
    def reel_count(self) -> int:
        return len(self.result.reels) if self.result else 0

    @classmethod
    def by_reels(cls, profiles: list[WatchedProfile]) -> list[WatchedProfile]:

        def views(p: WatchedProfile) -> int:
            return sum(r.views for r in p.result.reels) if p.result else 0

        by_views = sorted(profiles, key=views, reverse=True)
        by_count = sorted(by_views, key=lambda p: p.reel_count, reverse=True)
        return sorted(by_count, key=lambda p: p.pending, reverse=True)


class Leaderboard(BaseModel):
    rows: list[WatchedProfile]
    top_views: str | None
    top_reels: str | None

    @classmethod
    def of(cls, profiles: list[WatchedProfile]) -> Leaderboard:
        top_views = WatchedProfile.most_viewed(profiles)
        top_reels = WatchedProfile.most_reels(profiles, top_views)
        leaders = [u for u in (top_views, top_reels) if u]
        by_name = {p.username: p for p in profiles}
        rest = [p for p in profiles if p.username not in leaders]
        return cls(
            rows=[by_name[u] for u in leaders] + WatchedProfile.by_reels(rest),
            top_views=top_views,
            top_reels=top_reels,
        )


class FeedItem(BaseModel):
    username: str
    reel: Reel

    @classmethod
    def of(cls, profiles: list[WatchedProfile]) -> list[FeedItem]:
        items = [
            cls(username=p.username, reel=reel)
            for p in profiles
            if p.result
            for reel in p.result.reels
        ]
        items.sort(key=lambda i: i.reel.views, reverse=True)
        return items


def _group_name(value: str) -> str:
    return " ".join(value.split()).lower()[:64]


GroupName = Annotated[str, AfterValidator(_group_name)]


class GroupForm(BaseModel):
    group: GroupName = ""


class WatchForm(BaseModel):
    profiles: str
    group: GroupName = ""

    @property
    def usernames(self) -> list[str]:
        handles: list[str] = []
        bad: list[str] = []
        for line in self.profiles.splitlines():
            first = line.split()[0] if line.split() else ""
            if not first:
                continue
            try:
                handles.append(ProfileHandle(username=first).username)
            except ValidationError:  # silent: a bad line is listed back
                bad.append(line.strip())
        if bad:
            msg = "Not an Instagram profile: " + ", ".join(bad[:5])
            raise ValueError(msg)
        return list(dict.fromkeys(handles))
