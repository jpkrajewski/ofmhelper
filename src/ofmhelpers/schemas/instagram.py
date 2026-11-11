"""Instagram's plain shapes every layer shares: a profile handle as typed, a
profile, a reel and one profile's scan as /reels stores and shows them. The
client that fills them (and the GraphQL wire models) is
`integrations/instagram`, which re-exports these."""

import re
from statistics import median
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, computed_field, field_validator

# Instagram usernames: letters, digits, "." and "_", at most 30 characters.
_USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")


# A reel's shortcode is its media id written in base 64 over this alphabet.
_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


# Where a reel's mp4 may be fetched from. The page hands these URLs back to
# "Clone selected", so anything else is refused rather than fetched.
CDN_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")


# Instagram's anycast CDN host. The per-region hosts it hands out
# (`instagram.ftpa1-2.fna.fbcdn.net`, `scontent-xyz.cdninstagram.com`) serve
# the same signed paths, but some have only an IPv6 address, so an
# IPv4-only viewer (or container) cannot resolve them. Verified 2026-10-19:
# thumbnails and mp4s of such a host load from this one with 200.
GLOBAL_CDN_HOST = "scontent.cdninstagram.com"


def _global_cdn(value: str) -> str:
    """A CDN URL moved onto `GLOBAL_CDN_HOST`; anything else as it is."""
    parts = urlsplit(value)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(
        CDN_HOST_SUFFIXES
    ):
        return value
    return parts._replace(netloc=GLOBAL_CDN_HOST).geturl()


# A media URL from Instagram, always on the global CDN host.
MediaUrl = Annotated[str, AfterValidator(_global_cdn)]


class ProfileHandle(BaseModel):
    """One line of the /reels profile box: "name", "@name" or a profile URL,
    reduced to the bare username."""

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
    # Defaulted: scans stored before these were read have none.
    full_name: str = ""
    following: int | None = None
    is_verified: bool = False
    # Signed CDN URL that expires within days: only the latest scan's shows.
    avatar_url: MediaUrl = ""


# --- what /reels shows -------------------------------------------------------


# View-count rarity tiers, highest first, named like game loot: the card's
# border colour on /reels (static/css/pages.css keys off these names).
VIEW_TIERS = (
    (1_000_000, "legendary"),
    (500_000, "epic"),
    (200_000, "rare"),
    (100_000, "uncommon"),
    (50_000, "common"),
)


class Reel(BaseModel):
    """`video_url` / `duration_s` are None for a reel Instagram only shows to
    logged-in viewers when no working login was at hand: it is still listed
    (it is viral all the same), but cannot be played or cloned."""

    code: str
    taken_at: int
    duration_s: float | None
    views: int
    followers: int
    thumbnail_url: MediaUrl
    video_url: MediaUrl | None

    @property
    def pk(self) -> str:
        """Its media id, decoded from the shortcode."""
        pk = 0
        for char in self.code:
            pk = pk * 64 + _SHORTCODE_ALPHABET.index(char)
        return str(pk)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ratio(self) -> float:
        """Views per follower; how far past its own audience a reel went."""
        return self.views / max(self.followers, 1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def tier(self) -> str | None:
        """The highest VIEW_TIERS step its views reach; None below them all."""
        return next((name for floor, name in VIEW_TIERS if self.views >= floor), None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        return f"https://www.instagram.com/reel/{self.code}/"


class ReelStat(BaseModel):
    """Any reel of the scan window, eligible or not: what the stats are
    made of, kept so later statistics need no rescan."""

    code: str
    views: int
    posted_at: float


class ProfileReels(BaseModel):
    """What /reels lists for one profile: its recent reels short enough to
    clone and past the view floor (`reels`, most viewed first), every reel
    of the scan window (`recent`, for statistics), and the window's
    numbers. The computed numbers are dumped with it, so they are queryable
    in the stored JSON too."""

    profile: Profile
    scanned: int
    reels: list[Reel]
    window_days: float = 0
    recent: list[ReelStat] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def views_total(self) -> int:
        """Views of every reel posted in the window."""
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
        """Reels that made the feed."""
        return len(self.reels)
