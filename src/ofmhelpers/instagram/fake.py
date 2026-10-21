"""Instagram and its CDN, faked at the wire (OFM_FAKE_EXTERNALS, tests).

Answers the requests `client.InstagramClient` makes the way Instagram does
(the shapes in this package's CLAUDE.md), so the real client parses them:
the reels page's `profile_id`/`lsd`, the crawler page, the four GraphQL
queries by friendly name (paged by `first`/`after`), logged-in media info,
and CDN mp4/jpg bytes. Data: `fake_profiles.json`; every reel's id encodes a
post time `age_h` hours before the fake was made, as a real media id does.
An unknown username answers like a missing account (`ProfileNotFoundError`).

`FakeInstagram.proxy_modes` turns a proxy (its URL, or "" for direct) into a
failure: "dead" (ProxyError), "timeout" (ConnectTimeout), "rate_limited"
(429) or "banned" (profiles look missing, as on a flagged IP). `calls`
records (method, path, proxy) for assertions.
"""

from __future__ import annotations

import json
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import requests
from pydantic import BaseModel, Field

from ofmhelpers.instagram.models import GLOBAL_CDN_HOST
from ofmhelpers.utils import fake_media
from ofmhelpers.utils.outbound import FakeHandler, FakeReply

if TYPE_CHECKING:
    from collections.abc import Mapping

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


class FakeRow(BaseModel):
    """One reel as every query sees it."""

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

    def video_node(self) -> dict[str, Any]:
        return {
            "video_versions": [{"url": self.video}],
            "video_dash_manifest": (
                f'<MPD mediaPresentationDuration="PT{self.reel.duration_s}S"></MPD>'
            ),
            "taken_at": self.taken_at,
        }


def reel_code(username: str, index: int) -> str:
    return f"F{username.replace('_', '').replace('.', '')[:8]}{index:02d}"


def _pk(posted_at: float, profile: int, reel: int) -> str:
    """A media id: post time in the top bits, (profile, reel) in the low
    ones."""
    ms = int(posted_at * 1000) - _ID_EPOCH_MS
    return str((ms << _ID_TIME_SHIFT) | (profile << 8) | reel)


def _page(
    rows: list[FakeRow], variables: dict[str, Any]
) -> tuple[list[FakeRow], str | None]:
    first = int(variables.get("first") or 12)
    start = int(variables.get("after") or 0)
    end = start + first
    return rows[start:end], (str(end) if end < len(rows) else None)


def _connection(edges: list[dict[str, Any]], cursor: str | None) -> dict[str, Any]:
    return {
        "edges": edges,
        "page_info": {"end_cursor": cursor, "has_next_page": cursor is not None},
    }


class FakeInstagram:
    def __init__(self) -> None:
        data = _Dataset.model_validate_json(_DATA.read_text(encoding="utf-8"))
        self.profiles = {p.username: p for p in data.profiles}
        self._order = [p.username for p in data.profiles]
        # Ages count from here: ids stay the same for the process's life.
        self._now = time.time()
        self.proxy_modes: dict[str, str] = {}
        self.calls: list[tuple[str, str, str]] = []
        self._lock = threading.Lock()

    # --- data -------------------------------------------------------------

    def rows(self, profile: FakeProfile) -> list[FakeRow]:
        """Pinned first, then newest first, as the reels tab lists them."""
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

    # --- pages --------------------------------------------------------------

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

    # --- GraphQL -------------------------------------------------------------

    def _graphql(self, form: dict[str, str]) -> FakeReply:
        name = form.get("fb_api_req_friendly_name", "")
        variables = json.loads(form.get("variables") or "{}")
        body: dict[str, Any]
        match name:
            case "PolarisProfilePageContentQuery":
                body = {"data": {"user": self._user(str(variables.get("id")))}}
            case "PolarisProfileReelsTabContentQuery_connection":
                body = self._clips(variables)
            case "PolarisProfilePostsTabContentQuery_connection":
                body = self._timeline(variables)
            case "PolarisLoggedOutDesktopWWWPostRootContentQuery":
                body = self._media(str(variables.get("media_id")))
            case _:
                body = {"errors": [{"message": f"unknown query {name}"}]}
        return FakeReply.json_body(body)

    def _user(self, pk: str) -> dict[str, Any] | None:
        p = self._by_pk(pk)
        if p is None:
            return None
        avatar = f"{CDN}/avatar-{p.username}.jpg"
        return {
            "pk": p.pk,
            "username": p.username,
            "full_name": p.full_name,
            "follower_count": p.followers,
            "following_count": p.following,
            "is_private": p.is_private,
            "is_verified": p.is_verified,
            "profile_pic_url": avatar,
            "hd_profile_pic_url_info": {"url": avatar},
        }

    def _clips(self, variables: dict[str, Any]) -> dict[str, Any]:
        rows = self._visible_rows(self._by_pk(str(variables.get("id") or "")))
        chunk, cursor = _page(rows, variables)
        edges = [
            {
                "node": {
                    "media": {
                        "pk": r.pk,
                        "code": r.code,
                        "play_count": r.reel.views,
                        "image_versions2": {"candidates": [{"url": r.thumb}]},
                        "clips_tab_pinned_user_ids": r.pinned_by,
                    }
                }
            }
            for r in chunk
        ]
        return {
            "data": {
                "fetch__XDTUserDict": {"clips_connection": _connection(edges, cursor)}
            }
        }

    def _timeline(self, variables: dict[str, Any]) -> dict[str, Any]:
        rows = self._visible_rows(self.profiles.get(str(variables.get("username"))))
        chunk, cursor = _page(rows, variables)
        edges = [
            {
                "node": {
                    **r.video_node(),
                    "code": r.code,
                    "timeline_pinned_user_ids": r.pinned_by,
                }
            }
            for r in chunk
        ]
        return {
            "data": {
                "xdt_api__v1__feed__user_timeline_graphql_connection": _connection(
                    edges, cursor
                )
            }
        }

    def _media(self, pk: str) -> dict[str, Any]:
        row = self._find(pk)
        info = row.video_node() if row else None
        return {"data": {"xig_polaris_media": {"if_not_gated_logged_out": info}}}

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

    # --- transport ----------------------------------------------------------

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
    """A CDN object of the fake: an mp4 or a jpg (also served to the
    browser by the e2e suite)."""
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


def routes() -> dict[str, FakeHandler]:
    return {
        HOST: lambda request, proxies: fake_instagram().handle(request, proxies),
        GLOBAL_CDN_HOST: lambda request, _proxies: cdn_reply(request.url or ""),
    }
