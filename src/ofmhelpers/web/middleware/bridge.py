import re
import time
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
lazy from starlette.types import ASGIApp, Receive, Scope, Send

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.short_links.schema import SelfClick
from ofmhelpers.features.links import bridge as self_links
from ofmhelpers.log import get_logger
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)


class _Beacon(BaseModel):
    slug: str | None = None
    referrer: str | None = None


_SLUG = re.compile(r"^[a-z0-9._-]+$", re.IGNORECASE)
_TRACK_PATH = "/api/track-direct"
_BOT = re.compile(
    r"bot|crawler|spider|facebookexternalhit|embed|preview|slurp|scan|lighthouse",
    re.IGNORECASE,
)
_FB_APP = re.compile(r"FBAN|FBAV|FB_IAB|FBIOS", re.IGNORECASE)
_HEADERS = {
    "x-robots-tag": "noindex, nofollow, noarchive",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cache-control": "no-store",
}
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
    host = urlsplit(settings.web.base_url).hostname if settings.web.base_url else None
    if not host:
        return set()
    twin = host[4:] if host.startswith("www.") else f"www.{host}"
    return {host, twin}


def is_tunnel_host(host: str) -> bool:
    name = host.lower().rsplit(":", 1)[0] if host.count(":") == 1 else host.lower()
    return name.endswith(tuple(settings.links.tunnel_suffixes()))


def _is_bridge_host(host: str) -> bool:
    return is_tunnel_host(host) or host in set(self_links.active_hosts())


def _platform(ua: str) -> str | None:
    return next((name for name, pat in _PLATFORMS if re.search(pat, ua)), None)


def _device(ua: str) -> str | None:
    if re.search(r"iPad|Tablet", ua, re.IGNORECASE):
        return "tablet"
    if re.search(r"Mobi|Android|iPhone|iPod", ua, re.IGNORECASE):
        return "mobile"
    return "desktop" if ua else None


_BROWSERS = (
    ("Edge", r"Edg"),
    ("Opera", r"OPR|Opera"),
    ("Samsung", r"SamsungBrowser"),
    ("Firefox", r"Firefox|FxiOS"),
    ("Chrome", r"CriOS|Chrome"),
    ("Safari", r"Safari"),
)


def _browser(ua: str) -> str | None:
    if not ua:
        return None
    return next((name for name, pat in _BROWSERS if re.search(pat, ua)), "Other")


def _is_http(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def _cut(value: str | None, limit: int) -> str | None:
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
    limit = settings.links.bridge_rate_per_min
    key = f"bridge:rl:{_client_ip(request)}:{int(time.time() // 60)}"
    try:
        redis = get_redis()
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, 120)
    except RedisError:
        logger.warning("bridge: rate limit unavailable (Redis), allowed", exc_info=True)
        return True
    return count <= limit


class BridgeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        host = (request.url.hostname or "").lower()
        if host and host not in _app_hosts() and _is_bridge_host(host):
            response = await self._handle(request, host)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

    async def _handle(self, request: Request, host: str) -> Response:
        path = request.url.path
        if request.method == "POST" and path == _TRACK_PATH:
            return await self._track(request, host)
        if request.method in ("GET", "HEAD"):
            slug = path.strip("/")
            if slug and "/" not in slug and _SLUG.match(slug):
                return await self._page(request, host, slug)
        return await self._not_found(request)

    async def _not_found(self, request: Request) -> Response:
        response = await run_in_threadpool(
            render, request, "bridge.html", status_code=404, destination=None
        )
        response.headers.update(_HEADERS)
        return response

    async def _page(self, request: Request, host: str, slug: str) -> Response:
        ua = request.headers.get("user-agent", "")
        link = await run_in_threadpool(self_links.bridge_target, host, slug)
        if link is None or not _is_http(link.destination):
            return await self._not_found(request)
        if request.method == "GET" and not _BOT.search(ua) and _allow(request):
            await run_in_threadpool(_record, _visitor(request, "view", link.id, None))
        response = await run_in_threadpool(
            render,
            request,
            "bridge.html",
            destination=link.destination,
            slug=slug,
            blocked=bool(_FB_APP.search(ua)),
        )
        response.headers.update(_HEADERS)
        return response

    async def _track(self, request: Request, host: str) -> Response:
        try:
            body = _Beacon.model_validate_json(await request.body())
        except ValidationError:
            logger.debug("bridge: track body refused", exc_info=True)
            return Response(status_code=204, headers=_HEADERS)
        slug = (body.slug or "").strip("/")
        if slug and "/" not in slug and _SLUG.match(slug) and _allow(request):
            link = await run_in_threadpool(self_links.bridge_target, host, slug)
            if link is not None:
                await run_in_threadpool(
                    _record,
                    _visitor(request, "click", link.id, body.referrer),
                )
        return Response(status_code=204, headers=_HEADERS)
