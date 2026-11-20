import re
from statistics import median
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, computed_field, field_validator

_USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")


_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


CDN_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")


GLOBAL_CDN_HOST = "scontent.cdninstagram.com"


def _global_cdn(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(
        CDN_HOST_SUFFIXES
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
            text = urlsplit(text if "//" in text else f"https://{text}").path
            text = next((part for part in text.split("/") if part), "")
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
