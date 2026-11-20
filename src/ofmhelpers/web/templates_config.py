import hashlib
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
lazy from fastapi import Request

from ofmhelpers.auth.access import Action, Area, opens
from ofmhelpers.config import settings
from ofmhelpers.features.drive.posting import window as posting_window
from ofmhelpers.features.users.presence import idle_limit_s
from ofmhelpers.features.users.viewing import makers
from ofmhelpers.schemas.accounts import ago
from ofmhelpers.schemas.nav import nav, nav_of
from ofmhelpers.schemas.time_zones import TIME_ZONES, time_zone
lazy from ofmhelpers.features.users.viewing import Maker
lazy from ofmhelpers.schemas.users import Viewer

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"
TEMPLATE_RECHECK_S = 1.0


class TemplateFiles(FileSystemLoader):
    def get_source(self, environment: Environment, template: str):
        source, filename, uptodate = super().get_source(environment, template)
        checked_at = time.monotonic()
        current = True

        def is_current() -> bool:
            nonlocal checked_at, current
            if current and time.monotonic() - checked_at >= TEMPLATE_RECHECK_S:
                checked_at = time.monotonic()
                current = uptodate()
            return current

        return source, filename, is_current


@lru_cache(maxsize=256)
def _content_version(path: str, mtime_ns: int) -> str:
    del mtime_ns
    return hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:10]


def static_url(path: str) -> str:
    mtime_ns = (STATIC_DIR / path).stat().st_mtime_ns
    return f"/static/{path}?v={_content_version(path, mtime_ns)}"


def utc(ts: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return time.strftime(fmt, time.gmtime(ts))


def _viewer_of(request: Request) -> Viewer | None:
    return getattr(request.state, "viewer", None)


def _session_max_age(request: Request) -> int:
    viewer = _viewer_of(request)
    signed_in = (viewer.by or viewer.user) if viewer is not None else None
    return idle_limit_s(signed_in.role if signed_in is not None else None)


def _signed_in_at(request: Request) -> int | None:
    since = request.session.get("since") if "session" in request.scope else None
    return int(since) if since else None


def _makers(request: Request) -> list[Maker]:
    viewer = _viewer_of(request)
    return makers(viewer) if viewer is not None else []


def _can(request: Request, what: str) -> bool:
    viewer = _viewer_of(request)
    if viewer is None:
        return False
    if what in Action:
        return not viewer.read_only and opens(viewer, Action(what))
    return opens(viewer, Area(what))


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.loader = TemplateFiles(TEMPLATES_DIR)
    templates.env.auto_reload = not settings.infra.production

    templates.env.globals["session_max_age"] = _session_max_age
    templates.env.globals["static_url"] = static_url
    templates.env.globals["nav"] = nav
    templates.env.globals["nav_of"] = nav_of
    templates.env.globals["viewer_of"] = _viewer_of
    templates.env.globals["can"] = _can
    templates.env.globals["makers_of"] = _makers
    templates.env.globals["time_zones"] = TIME_ZONES
    templates.env.globals["time_zone"] = time_zone
    templates.env.globals["signed_in_at"] = _signed_in_at
    templates.env.globals["posting_window"] = posting_window
    templates.env.filters["utc"] = utc
    templates.env.filters["ago"] = ago
    return templates


def render(
    request: Request, name: str, status_code: int = 200, **context: Any
) -> HTMLResponse:
    return get_templates().TemplateResponse(
        request, name, context, status_code=status_code
    )


def render_polled(request: Request, name: str, **context: Any) -> Response:
    response = render(request, name, **context)
    etag = f'"{hashlib.sha256(response.body).hexdigest()[:20]}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return response


def render_partial(name: str, **context: Any) -> str:
    return get_templates().get_template(name).render(**context)
