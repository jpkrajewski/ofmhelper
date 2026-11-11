"""The parts of Instagram's logged-out web GraphQL this app reads, and what
/reels makes of them. Instagram only speaks JSON dicts; they are validated at
that boundary (`ProfilePage.model_validate(r.json())`, ...) and nothing past
it sees a raw dict."""

import re
from typing import Annotated

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
)

from ofmhelpers.schemas.instagram import (
    CDN_HOST_SUFFIXES,
    GLOBAL_CDN_HOST,
    VIEW_TIERS,
    MediaUrl,
    Profile,
    ProfileHandle,
    ProfileReels,
    Reel,
    ReelStat,
)

__all__ = [
    "CDN_HOST_SUFFIXES",
    "GLOBAL_CDN_HOST",
    "VIEW_TIERS",
    "MediaUrl",
    "Profile",
    "ProfileHandle",
    "ProfileReels",
    "Reel",
    "ReelStat",
]

# A DASH manifest's length, e.g. mediaPresentationDuration="PT1M4.5S".
_DASH_DURATION = re.compile(
    r'mediaPresentationDuration="PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?"'
)

# Instagram media ids: milliseconds since this epoch, shifted left 23 bits.
_ID_EPOCH_S = 1314220021.721
_ID_TIME_SHIFT = 23


def _instagram_cdn(value: AnyHttpUrl) -> AnyHttpUrl:
    host = value.host or ""
    if value.scheme != "https" or not host.endswith(CDN_HOST_SUFFIXES):
        msg = "Not an Instagram CDN URL"
        raise ValueError(msg)
    return value


CdnUrl = Annotated[AnyHttpUrl, AfterValidator(_instagram_cdn)]


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
    full_name: str = ""
    follower_count: int = 0
    following_count: int | None = None
    is_private: bool = False
    is_verified: bool = False
    profile_pic_url: str = ""
    hd_profile_pic_url_info: _Image | None = None

    @property
    def avatar_url(self) -> str:
        hd = self.hd_profile_pic_url_info
        return hd.url if hd else self.profile_pic_url


class _ProfileData(_Base):
    user: _ProfileUser | None = None


class ProfilePage(_Base):
    data: _ProfileData = _ProfileData()


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


# --- the uploaded login -------------------------------------------------------


class LoginTokens(BaseModel):
    """What a GraphQL query sent as the uploaded login carries, read off a
    page the login loaded: its viewer id (`av`), request token
    (`fb_dtsg`), `lsd`, and that page (the Referer)."""

    actor_id: str
    fb_dtsg: str
    lsd: str
    referer: str


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


class _GraphQLError(_Base):
    message: str = ""


class GraphQLErrors(_Base):
    """The `errors` any GraphQL answer may carry -- with HTTP 200, beside or
    instead of its data."""

    errors: list[_GraphQLError] = []

    @property
    def rate_limited(self) -> bool:
        """'Rate limit exceeded' (2026-10-23, the posts query): without this
        it read as a page with nothing on it."""
        return any("rate limit" in e.message.lower() for e in self.errors)


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

    video_url: MediaUrl
    duration_s: float
    taken_at: int

    @classmethod
    def from_page(cls, page: MediaPage) -> ReelMedia | None:
        """None when the reel has no video or no readable length -- gated
        for logged-out visitors (some accounts' reels all are), or removed."""
        return cls.from_dash(page.info) if page.info else None

    @classmethod
    def from_dash(cls, info: _MediaInfo) -> ReelMedia | None:
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
    def from_info(cls, info: MediaInfo) -> ReelMedia | None:
        item = info.items[0] if info.items else None
        if item is None or not item.video_versions or item.video_duration is None:
            return None
        return cls(
            video_url=item.video_versions[0].url,
            duration_s=item.video_duration,
            taken_at=item.taken_at,
        )


class ReelDownload(BaseModel):
    """One reel "Clone selected" fetches: its code (the saved file's name)
    and its mp4 on Instagram's CDN."""

    code: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    video_url: CdnUrl


class TimelinePost(BaseModel):
    """One post of the posts grid: `media` is its mp4 + length, None for a
    photo."""

    code: str
    media: ReelMedia | None
    posted_at: float
    pinned: bool
