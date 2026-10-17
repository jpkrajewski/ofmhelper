"""The parts of Instagram's logged-out web GraphQL this app reads, and what
/reels makes of them. Instagram only speaks JSON dicts; they are validated at
that boundary (`ProfilePage.model_validate(r.json())`, ...) and nothing past
it sees a raw dict."""

import re
from statistics import median
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
)

# Instagram usernames: letters, digits, "." and "_", at most 30 characters.
_USERNAME = re.compile(r"[A-Za-z0-9._]{1,30}")

# A DASH manifest's length, e.g. mediaPresentationDuration="PT1M4.5S".
_DASH_DURATION = re.compile(
    r'mediaPresentationDuration="PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?"'
)

# Instagram media ids: milliseconds since this epoch, shifted left 23 bits.
_ID_EPOCH_S = 1314220021.721
_ID_TIME_SHIFT = 23

# Where a reel's mp4 may be fetched from. The page hands these URLs back to
# "Clone selected", so anything else is refused rather than fetched.
CDN_HOST_SUFFIXES = (".cdninstagram.com", ".fbcdn.net")


def _instagram_cdn(value: AnyHttpUrl) -> AnyHttpUrl:
    host = value.host or ""
    if value.scheme != "https" or not host.endswith(CDN_HOST_SUFFIXES):
        msg = "Not an Instagram CDN URL"
        raise ValueError(msg)
    return value


CdnUrl = Annotated[AnyHttpUrl, AfterValidator(_instagram_cdn)]


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


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Image(_Base):
    url: str


class _ImageVersions(_Base):
    candidates: list[_Image] = []

    @property
    def first(self) -> str:
        return self.candidates[0].url if self.candidates else ""


# --- PolarisProfilePageContentQuery -----------------------------------------


class _ProfileUser(_Base):
    pk: str
    username: str
    follower_count: int = 0
    is_private: bool = False


class _ProfileData(_Base):
    user: _ProfileUser | None = None


class ProfilePage(_Base):
    data: _ProfileData = _ProfileData()


class Profile(BaseModel):
    user_id: str
    username: str
    followers: int
    is_private: bool


# --- PolarisProfileReelsTabContentQuery_connection --------------------------


class ReelSummary(_Base):
    """A reel as the profile's reels tab lists it: no length, no video yet."""

    pk: str
    code: str
    play_count: int | None = None
    image_versions2: _ImageVersions = _ImageVersions()
    # null on some accounts' reels.
    clips_tab_pinned_user_ids: list[str] | None = None

    @property
    def posted_at(self) -> float:
        """Unix time it was posted: a media id's top bits are milliseconds
        since Instagram's id epoch (matches `taken_at` to within seconds)."""
        return (int(self.pk) >> _ID_TIME_SHIFT) / 1000 + _ID_EPOCH_S

    @property
    def pinned(self) -> bool:
        """Pinned reels head the tab whatever their age."""
        return bool(self.clips_tab_pinned_user_ids)


class _ClipNode(_Base):
    media: ReelSummary


class _ClipEdge(_Base):
    node: _ClipNode


class _PageInfo(_Base):
    end_cursor: str | None = None
    has_next_page: bool = False


class _ClipsConnection(_Base):
    edges: list[_ClipEdge] = []
    page_info: _PageInfo = _PageInfo()


class _ClipsUser(_Base):
    clips_connection: _ClipsConnection = _ClipsConnection()


class _ClipsData(_Base):
    user: _ClipsUser | None = Field(default=None, alias="fetch__XDTUserDict")


class ClipsPage(_Base):
    data: _ClipsData = _ClipsData()

    @property
    def reels(self) -> list[ReelSummary]:
        user = self.data.user
        return [e.node.media for e in user.clips_connection.edges] if user else []

    @property
    def next_cursor(self) -> str | None:
        user = self.data.user
        info = user.clips_connection.page_info if user else _PageInfo()
        return info.end_cursor if info.has_next_page else None


# --- PolarisLoggedOutDesktopWWWPostRootContentQuery -------------------------


class _MediaInfo(_Base):
    # null on a photo.
    video_versions: list[_Image] | None = None
    video_dash_manifest: str | None = None
    taken_at: int = 0


class _PolarisMedia(_Base):
    info: _MediaInfo | None = Field(default=None, alias="if_not_gated_logged_out")


class _MediaData(_Base):
    media: _PolarisMedia | None = Field(default=None, alias="xig_polaris_media")


class MediaPage(_Base):
    data: _MediaData = _MediaData()

    @property
    def info(self) -> _MediaInfo | None:
        return self.data.media.info if self.data.media else None


# --- PolarisProfilePostsTabContentQuery_connection --------------------------


class _TimelineNode(_MediaInfo):
    code: str
    timeline_pinned_user_ids: list[str] | None = None


class _TimelineEdge(_Base):
    node: _TimelineNode


class _TimelineConnection(_Base):
    edges: list[_TimelineEdge] = []
    page_info: _PageInfo = _PageInfo()


class _TimelineData(_Base):
    feed: _TimelineConnection | None = Field(
        default=None, alias="xdt_api__v1__feed__user_timeline_graphql_connection"
    )


class TimelinePage(_Base):
    """One page of a profile's posts grid: photos and videos, newest first
    after the pinned ones, each video with its mp4 and DASH manifest. No view
    counts (the reels tab has those). Hidden-from-visitors accounts answer
    without `feed` (an error instead)."""

    data: _TimelineData | None = None

    @property
    def feed(self) -> _TimelineConnection:
        return (self.data.feed if self.data else None) or _TimelineConnection()

    @property
    def nodes(self) -> list[_TimelineNode]:
        return [e.node for e in self.feed.edges]

    @property
    def next_cursor(self) -> str | None:
        info = self.feed.page_info
        return info.end_cursor if info.has_next_page else None


# --- GET /api/v1/media/{pk}/info/ (logged in) -------------------------------


class _InfoItem(_Base):
    video_versions: list[_Image] = []
    video_duration: float | None = None
    taken_at: int = 0


class MediaInfo(_Base):
    items: list[_InfoItem] = []


class ReelMedia(BaseModel):
    """A reel's playable side: its mp4 and length."""

    video_url: str
    duration_s: float
    taken_at: int

    @classmethod
    def from_page(cls, page: MediaPage) -> "ReelMedia | None":
        """None when the reel has no video or no readable length -- gated
        for logged-out visitors (some accounts' reels all are), or removed."""
        return cls.from_dash(page.info) if page.info else None

    @classmethod
    def from_dash(cls, info: "_MediaInfo") -> "ReelMedia | None":
        """A logged-out media node (reel query or timeline): length from its
        DASH manifest. None for a photo, or a video without a manifest."""
        if not info.video_versions:
            return None
        match = _DASH_DURATION.search(info.video_dash_manifest or "")
        if match is None:
            return None
        hours, minutes, seconds = (float(g or 0) for g in match.groups())
        return cls(
            video_url=info.video_versions[0].url,
            duration_s=hours * 3600 + minutes * 60 + seconds,
            taken_at=info.taken_at,
        )

    @classmethod
    def from_info(cls, info: MediaInfo) -> "ReelMedia | None":
        item = info.items[0] if info.items else None
        if item is None or not item.video_versions or item.video_duration is None:
            return None
        return cls(
            video_url=item.video_versions[0].url,
            duration_s=item.video_duration,
            taken_at=item.taken_at,
        )


# --- what /reels shows -------------------------------------------------------


# View-count rarity tiers, highest first, named like game loot: the card's
# border colour on /reels (static/css/app.css keys off these names).
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
    thumbnail_url: str
    video_url: str | None

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


class ReelDownload(BaseModel):
    """One reel "Clone selected" fetches: its code (the saved file's name)
    and its mp4 on Instagram's CDN."""

    code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    video_url: CdnUrl
