import json
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit
lazy from collections.abc import Mapping

import requests
from pydantic import BaseModel, Field

from ofmhelpers.integrations.instagram.models import GLOBAL_CDN_HOST
from ofmhelpers.integrations.outbound import FakeReply, FakeRoute
from ofmhelpers.media import fake_media

HOST = "www.instagram.com"
CDN = f"https://{GLOBAL_CDN_HOST}/fake"
_ID_EPOCH_MS = 1314220021721
_ID_TIME_SHIFT = 23
_LSD = "fake-lsd-token"
_ACTOR = "17841400000000000"
_DATA = Path(__file__).with_name("fake_profiles.json")
_SLOW_DOWN = "Please wait a few minutes before you try again."


class FakeReel(BaseModel):
    views: int
    age_h: float
    duration_s: float
    pinned: bool = False


class FakeProfile(BaseModel):
    username: str
    pk: str
    full_name: str = ""
    followers: int = 0
    following: int = 0
    is_private: bool = False
    is_verified: bool = False
    reels: list[FakeReel] = Field(default_factory=list)


class _Dataset(BaseModel):
    profiles: list[FakeProfile]


class FakeVideoVersion(BaseModel):
    url: str


class FakeVideoNode(BaseModel):
    video_versions: list[FakeVideoVersion]
    video_dash_manifest: str
    taken_at: int


class FakeRow(BaseModel):
    reel: FakeReel
    owner_pk: str
    pk: str
    code: str
    taken_at: int

    @property
    def thumb(self) -> str:
        return f"{CDN}/{self.code}.jpg"

    @property
    def video(self) -> str:
        return f"{CDN}/{self.code}.mp4"

    @property
    def pinned_by(self) -> list[str] | None:
        return [self.owner_pk] if self.reel.pinned else None

    def video_node(self) -> FakeVideoNode:
        return FakeVideoNode(
            video_versions=[FakeVideoVersion(url=self.video)],
            video_dash_manifest=(
                f'<MPD mediaPresentationDuration="PT{self.reel.duration_s}S"></MPD>'
            ),
            taken_at=self.taken_at,
        )


def reel_code(username: str, index: int) -> str:
    return f"F{username.replace('_', '').replace('.', '')[:8]}{index:02d}"


def _pk(posted_at: float, profile: int, reel: int) -> str:
    ms = int(posted_at * 1000) - _ID_EPOCH_MS
    return str((ms << _ID_TIME_SHIFT) | (profile << 8) | reel)


class _Page(BaseModel, frozen=True):
    rows: list[FakeRow]
    cursor: str | None


def _page(rows: list[FakeRow], variables: dict[str, Any]) -> _Page:
    first = int(variables.get("first") or 12)
    start = int(variables.get("after") or 0)
    end = start + first
    return _Page(rows=rows[start:end], cursor=str(end) if end < len(rows) else None)


class _Image(BaseModel):
    url: str


class _Images(BaseModel):
    candidates: list[_Image]


class _ClipMedia(BaseModel):
    pk: str
    code: str
    play_count: int
    image_versions2: _Images
    clips_tab_pinned_user_ids: list[str] | None


class _ClipNode(BaseModel):
    media: _ClipMedia


class _ClipEdge(BaseModel):
    node: _ClipNode


class _TimelineNode(FakeVideoNode):
    code: str
    timeline_pinned_user_ids: list[str] | None


class _TimelineEdge(BaseModel):
    node: _TimelineNode


class _PageInfo(BaseModel):
    end_cursor: str | None
    has_next_page: bool


class _Connection[E: BaseModel](BaseModel):
    edges: list[E]
    page_info: _PageInfo

    @classmethod
    def of(cls, edges: list[E], cursor: str | None) -> Self:
        return cls(
            edges=edges,
            page_info=_PageInfo(end_cursor=cursor, has_next_page=cursor is not None),
        )


class _UserNode(BaseModel):
    pk: str
    username: str
    full_name: str
    follower_count: int
    following_count: int
    is_private: bool
    is_verified: bool
    profile_pic_url: str
    hd_profile_pic_url_info: _Image


class _UserData(BaseModel):
    user: _UserNode | None


class _UserBody(BaseModel):
    data: _UserData


class _ClipsUser(BaseModel):
    clips_connection: _Connection[_ClipEdge]


class _ClipsData(BaseModel):
    clips_user: _ClipsUser = Field(serialization_alias="fetch__XDTUserDict")


class _ClipsBody(BaseModel):
    data: _ClipsData


class _TimelineData(BaseModel):
    xdt_api__v1__feed__user_timeline_graphql_connection: _Connection[_TimelineEdge]


class _TimelineBody(BaseModel):
    data: _TimelineData


class _GatedMedia(BaseModel):
    if_not_gated_logged_out: FakeVideoNode | None


class _MediaData(BaseModel):
    xig_polaris_media: _GatedMedia


class _MediaBody(BaseModel):
    data: _MediaData


class _ErrorMessage(BaseModel):
    message: str


class _ErrorBody(BaseModel):
    errors: list[_ErrorMessage]


class FakeInstagram:
    def __init__(self) -> None:
        data = _Dataset.model_validate_json(_DATA.read_text(encoding="utf-8"))
        self.profiles = {p.username: p for p in data.profiles}
        self._order = [p.username for p in data.profiles]
        self._now = time.time()
        self.proxy_modes: dict[str, str] = {}
        self.calls: list[tuple[str, str, str]] = []
        self._lock = threading.Lock()

    def rows(self, profile: FakeProfile) -> list[FakeRow]:
        ordinal = self._order.index(profile.username)
        rows = []
        for i, reel in enumerate(profile.reels):
            posted = self._now - reel.age_h * 3600
            rows.append(
                FakeRow(
                    reel=reel,
                    owner_pk=profile.pk,
                    pk=_pk(posted, ordinal, i),
                    code=reel_code(profile.username, i),
                    taken_at=int(posted),
                )
            )
        rows.sort(key=lambda r: (not r.reel.pinned, -r.taken_at))
        return rows

    def _by_pk(self, pk: str) -> FakeProfile | None:
        return next((p for p in self.profiles.values() if p.pk == pk), None)

    def _visible_rows(self, profile: FakeProfile | None) -> list[FakeRow]:
        return [] if profile is None or profile.is_private else self.rows(profile)

    def _find(self, pk: str) -> FakeRow | None:
        return next(
            (r for p in self.profiles.values() for r in self.rows(p) if r.pk == pk),
            None,
        )

    def _profile_page(
        self, username: str, *, banned: bool, logged_in: bool
    ) -> FakeReply:
        profile = self.profiles.get(username)
        if profile is None or banned:
            return FakeReply.text('<html><script>"lsd":"x"</script></html>')
        login = (
            f'"actorID":"{_ACTOR}","DTSGInitialData",[],{{"token":"fake-dtsg"}}'
            if logged_in
            else '"actorID":null'
        )
        return FakeReply.text(
            f'<html><script>{{"profile_id":"{profile.pk}"}} '
            f'["LSD",[],{{"token":"{_LSD}"}}] {login} "pageID":"profilePage"'
            "</script></html>"
        )

    def _crawler_page(self, username: str, *, banned: bool) -> FakeReply:
        if username in self.profiles and not banned:
            return FakeReply.text('<meta property="og:type" content="profile" />')
        return FakeReply.text("<html><title>Instagram</title></html>")

    def _graphql(self, form: dict[str, str]) -> FakeReply:
        name = form.get("fb_api_req_friendly_name", "")
        variables = json.loads(form.get("variables") or "{}")
        body: BaseModel
        match name:
            case "PolarisProfilePageContentQuery":
                body = self._user(str(variables.get("id")))
            case "PolarisProfileReelsTabContentQuery_connection":
                body = self._clips(variables)
            case "PolarisProfilePostsTabContentQuery_connection":
                body = self._timeline(variables)
            case "PolarisLoggedOutDesktopWWWPostRootContentQuery":
                body = self._media(str(variables.get("media_id")))
            case _:
                body = _ErrorBody(
                    errors=[_ErrorMessage(message=f"unknown query {name}")]
                )
        return FakeReply.json_body(body.model_dump(mode="json", by_alias=True))

    def _user(self, pk: str) -> _UserBody:
        p = self._by_pk(pk)
        if p is None:
            return _UserBody(data=_UserData(user=None))
        avatar = f"{CDN}/avatar-{p.username}.jpg"
        return _UserBody(
            data=_UserData(
                user=_UserNode(
                    pk=p.pk,
                    username=p.username,
                    full_name=p.full_name,
                    follower_count=p.followers,
                    following_count=p.following,
                    is_private=p.is_private,
                    is_verified=p.is_verified,
                    profile_pic_url=avatar,
                    hd_profile_pic_url_info=_Image(url=avatar),
                )
            )
        )

    def _clips(self, variables: dict[str, Any]) -> _ClipsBody:
        rows = self._visible_rows(self._by_pk(str(variables.get("id") or "")))
        page = _page(rows, variables)
        edges = [
            _ClipEdge(
                node=_ClipNode(
                    media=_ClipMedia(
                        pk=r.pk,
                        code=r.code,
                        play_count=r.reel.views,
                        image_versions2=_Images(candidates=[_Image(url=r.thumb)]),
                        clips_tab_pinned_user_ids=r.pinned_by,
                    )
                )
            )
            for r in page.rows
        ]
        return _ClipsBody(
            data=_ClipsData(
                clips_user=_ClipsUser(
                    clips_connection=_Connection[_ClipEdge].of(edges, page.cursor)
                )
            )
        )

    def _timeline(self, variables: dict[str, Any]) -> _TimelineBody:
        rows = self._visible_rows(self.profiles.get(str(variables.get("username"))))
        page = _page(rows, variables)
        edges = [
            _TimelineEdge(
                node=_TimelineNode(
                    **r.video_node().model_dump(),
                    code=r.code,
                    timeline_pinned_user_ids=r.pinned_by,
                )
            )
            for r in page.rows
        ]
        return _TimelineBody(
            data=_TimelineData(
                xdt_api__v1__feed__user_timeline_graphql_connection=_Connection[
                    _TimelineEdge
                ].of(edges, page.cursor)
            )
        )

    def _media(self, pk: str) -> _MediaBody:
        row = self._find(pk)
        return _MediaBody(
            data=_MediaData(
                xig_polaris_media=_GatedMedia(
                    if_not_gated_logged_out=row.video_node() if row else None
                )
            )
        )

    def _media_info(self, pk: str) -> FakeReply:
        row = self._find(pk)
        if row is None:
            return FakeReply.json_body({"message": "Media not found"}, status=404)
        return FakeReply.json_body(
            {
                "items": [
                    {
                        "video_versions": [{"url": row.video}],
                        "video_duration": row.reel.duration_s,
                        "taken_at": row.taken_at,
                    }
                ]
            }
        )

    def _failure(
        self, mode: str, proxy: str, request: requests.PreparedRequest
    ) -> FakeReply | None:
        if mode == "dead":
            msg = f"fake: proxy {proxy} is dead"
            raise requests.exceptions.ProxyError(msg, request=request)
        if mode == "timeout":
            msg = f"fake: proxy {proxy} timed out"
            raise requests.exceptions.ConnectTimeout(msg, request=request)
        if mode == "rate_limited":
            return FakeReply.text(_SLOW_DOWN, 429)
        return None

    def handle(
        self, request: requests.PreparedRequest, proxies: Mapping[str, str]
    ) -> FakeReply:
        url = urlsplit(request.url or "")
        proxy = proxies.get("https") or proxies.get("http") or ""
        with self._lock:
            self.calls.append((request.method or "", url.path, proxy))
        mode = self.proxy_modes.get(proxy, "ok")
        failed = self._failure(mode, proxy, request)
        if failed is not None:
            return failed
        banned = mode == "banned"
        logged_in = "sessionid=" in (request.headers.get("Cookie") or "")
        if request.method == "POST" and url.path in ("/api/graphql", "/graphql/query"):
            if banned:
                return FakeReply.text(_SLOW_DOWN, 401)
            body = request.body or ""
            text = body.decode() if isinstance(body, bytes) else str(body)
            return self._graphql({k: v[0] for k, v in parse_qs(text).items()})
        crawler = "facebookexternalhit" in (request.headers.get("User-Agent") or "")
        match [p for p in url.path.split("/") if p]:
            case ["api", "v1", "media", pk, *_]:
                return self._media_info(pk)
            case [username, "reels"]:
                return self._profile_page(username, banned=banned, logged_in=logged_in)
            case [username] if crawler:
                return self._crawler_page(username, banned=banned)
            case [username]:
                return self._profile_page(username, banned=banned, logged_in=logged_in)
        return FakeReply.text("Page not found", 404)


def cdn_reply(url: str) -> FakeReply:
    path = urlsplit(url).path
    if path.endswith(".mp4"):
        return FakeReply(body=fake_media.mp4(), headers={"Content-Type": "video/mp4"})
    if path.endswith(".jpg"):
        return FakeReply(
            body=fake_media.image(), headers={"Content-Type": "image/jpeg"}
        )
    return FakeReply.text("Not found", 404)


@lru_cache(maxsize=1)
def fake_instagram() -> FakeInstagram:
    return FakeInstagram()


def routes() -> list[FakeRoute]:
    return [
        FakeRoute(
            host=HOST,
            handler=lambda request, proxies: fake_instagram().handle(request, proxies),
        ),
        FakeRoute(
            host=GLOBAL_CDN_HOST,
            handler=lambda request, _proxies: cdn_reply(request.url or ""),
        ),
    ]
