"""The public bridge: self-hosted link pages on our own custom domains.

Outermost middleware, so a request to one of our bridge domains never touches
the session/auth stack. On any other host -- the app's own domain, the
loopback /health probe, a test's `testserver` -- it does nothing and the app
serves the request as usual. The active-host list is cached (this runs on
every request), and a request to the app's own host skips even that lookup.

A bridge page is an 18+ gate that pops the visitor out of an in-app browser
(Instagram, X, Telegram, the Facebook app) into their real browser, straight
to the destination -- the same flow the rented providers use, but every hit is
logged to our own click table with the visitor's Cloudflare geo. The page is
byte-identical for every visitor and for a crawler (we never show reviewers a
different destination): the only thing a crawler changes is that its view is
not counted."""

import re
import time
from typing import Literal
from urllib.parse import urlsplit

from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
lazy from starlette.types import ASGIApp, Receive, Scope, Send

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.stores import self_links
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.self_links import SelfClick
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)

_SLUG = re.compile(r"^[a-z0-9._-]+$", re.IGNORECASE)
_TRACK_PATH = "/api/track-direct"
# A crawler/scanner: its view is not counted (the page it gets is the same).
_BOT = re.compile(
    r"bot|crawler|spider|facebookexternalhit|embed|preview|slurp|scan|lighthouse",
    re.IGNORECASE,
)
# The Facebook app's in-app browser: JS cannot pop it open, so we serve the
# "open in your browser" page that re-opens the link in the real browser (the
# gate and its tracking then run there).
_FB_APP = re.compile(r"FBAN|FBAV|FB_IAB|FBIOS", re.IGNORECASE)
# Kept off every search index and sent with no referrer or caching.
_HEADERS = {
    "x-robots-tag": "noindex, nofollow, noarchive",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}
# In-app browser (matched against the UA) -> the analytics label we store.
_PLATFORMS = (
    ("instagram", "Instagram"),
    ("tiktok", "TikTok"),
    ("telegram", "Telegram"),
    ("snapchat", "Snapchat"),
    ("reddit", "Reddit"),
    ("facebook", r"FBAN|FBAV|FB_IAB|FBIOS|facebook"),
    ("x", r"Twitter|\bX\b"),
)


def _app_hosts() -> set[str]:
    """The app's own hostnames (apex and www twin): never a bridge domain, so
    a request to one skips the host lookup."""
    host = urlsplit(settings.web.base_url).hostname if settings.web.base_url else None
    if not host:
        return set()
    twin = host[4:] if host.startswith("www.") else f"www.{host}"
    return {host, twin}


def _platform(ua: str) -> str | None:
    return next((name for name, pat in _PLATFORMS if re.search(pat, ua)), None)


def _device(ua: str) -> str | None:
    if re.search(r"iPad|Tablet", ua, re.IGNORECASE):
        return "tablet"
    if re.search(r"Mobi|Android|iPhone|iPod", ua, re.IGNORECASE):
        return "mobile"
    return "desktop" if ua else None


# Order matters: Edge/Opera UAs contain "Chrome"; Chrome contains "Safari".
_BROWSERS = (
    ("Edge", r"Edg"),
    ("Opera", r"OPR|Opera"),
    ("Samsung", r"SamsungBrowser"),
    ("Firefox", r"Firefox|FxiOS"),
    ("Chrome", r"CriOS|Chrome"),
    ("Safari", r"Safari"),
)


def _browser(ua: str) -> str | None:
    """The browser label, computed once at write time (like platform/device),
    so the stats page groups a column instead of re-parsing every UA."""
    if not ua:
        return None
    return next((name for name, pat in _BROWSERS if re.search(pat, ua)), "Other")


def _is_http(url: str) -> bool:
    """Defence in depth: the destination is an http(s) target-page URL upstream
    (HttpUrl + Target.problem), but the bridge never hands a javascript:/data:
    URL to the page even if a future write path forgot to validate."""
    return url.lower().startswith(("http://", "https://"))


def _cut(value: str | None, limit: int) -> str | None:
    """Trim an attacker-settable header/body field so one flooded row can't
    carry kilobytes."""
    return value[:limit] if value else value


def _visitor(
    request: Request,
    kind: Literal["view", "click"],
    link_id: int,
    referrer: str | None,
) -> SelfClick:
    h = request.headers
    ua = h.get("user-agent", "")
    client = request.client.host if request.client else None
    return SelfClick(
        link_id=link_id,
        kind=kind,
        ip=_cut(h.get("cf-connecting-ip") or client, 64),
        country=_cut(h.get("cf-ipcountry") or None, 8),
        region=_cut(h.get("cf-region") or None, 64),
        city=_cut(h.get("cf-ipcity") or None, 128),
        tz=_cut(h.get("cf-timezone") or None, 64),
        referrer=_cut(referrer or h.get("referer") or None, 2048),
        platform=_platform(ua),
        device=_device(ua),
        browser=_browser(ua),
        ua=_cut(ua, 1000) or None,
    )


def _record(click: SelfClick) -> None:
    try:
        self_links.record_click(click)
    except Exception:
        logger.warning("bridge %s not recorded", click.kind, exc_info=True)


def _client_ip(request: Request) -> str:
    client = request.client.host if request.client else None
    return request.headers.get("cf-connecting-ip") or client or "?"


def _allow(request: Request) -> bool:
    """A per-IP, per-minute cap on the public endpoints (the bridge runs
    outside the app's write rate limiter). Over the cap the hit is still
    served -- only the recording is skipped, so a flood can neither grow the
    table nor poison the stats. Redis down: fail open."""
    limit = settings.links.bridge_rate_per_min
    key = f"bridge:rl:{_client_ip(request)}:{int(time.time() // 60)}"
    try:
        redis = get_redis()
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, 120)
    except RedisError:
        return True
    return count <= limit


class BridgeMiddleware:
    """Serves the bridge pages and the click beacon on our bridge domains."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        host = (request.url.hostname or "").lower()
        if host and host not in _app_hosts() and host in set(self_links.active_hosts()):
            response = await self._handle(request, host)
            if response is not None:
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

    async def _handle(self, request: Request, host: str) -> Response | None:
        path = request.url.path
        if request.method == "POST" and path == _TRACK_PATH:
            return await self._track(request, host)
        if request.method in ("GET", "HEAD"):
            slug = path.strip("/")
            if slug and "/" not in slug and _SLUG.match(slug):
                return await self._page(request, host, slug)
        # Any other path (static, favicon, the beacon's preflight) is the app's.
        return None

    async def _page(self, request: Request, host: str, slug: str) -> Response:
        # The DB lookup, the insert and the Jinja render are all blocking; a
        # pure-ASGI middleware is not offloaded by Starlette, so run them off
        # the event loop or one slow hit stalls every request in the process.
        ua = request.headers.get("user-agent", "")
        link = await run_in_threadpool(self_links.bridge_target, host, slug)
        if link is None or not _is_http(link["destination"]):
            response = await run_in_threadpool(
                render, request, "bridge.html", status_code=404, destination=None
            )
            response.headers.update(_HEADERS)
            return response
        if request.method == "GET" and not _BOT.search(ua) and _allow(request):
            await run_in_threadpool(
                _record, _visitor(request, "view", link["id"], None)
            )
        response = await run_in_threadpool(
            render,
            request,
            "bridge.html",
            destination=link["destination"],
            slug=slug,
            blocked=bool(_FB_APP.search(ua)),
        )
        response.headers.update(_HEADERS)
        return response

    async def _track(self, request: Request, host: str) -> Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        slug = str(body.get("slug") or "").strip("/")
        if slug and "/" not in slug and _SLUG.match(slug) and _allow(request):
            link = await run_in_threadpool(self_links.bridge_target, host, slug)
            if link is not None:
                await run_in_threadpool(
                    _record,
                    _visitor(request, "click", link["id"], body.get("referrer")),
                )
        return Response(status_code=204, headers=_HEADERS)
