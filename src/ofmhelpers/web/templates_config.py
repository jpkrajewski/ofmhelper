import hashlib
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from ofmhelpers.config import settings
from ofmhelpers.web.schemas.accounts import ago
from ofmhelpers.web.schemas.nav import nav

STATIC_DIR = Path(__file__).parent / "static"


@lru_cache(maxsize=256)
def _content_version(path: str, mtime_ns: int) -> str:
    """Short content hash of one static file. Keyed on mtime too, so an edit
    under the dev reloader gets a new version without a restart."""
    del mtime_ns
    return hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:10]


def static_url(path: str) -> str:
    """`/static/<path>?v=<content hash>` -- every page links its CSS/JS through
    this. nginx caches /static for a week, so an unversioned URL keeps serving
    browsers the old file after a deploy (an old clone.js against the new
    /clone/run). The version changes exactly when the file does."""
    mtime_ns = (STATIC_DIR / path).stat().st_mtime_ns
    return f"/static/{path}?v={_content_version(path, mtime_ns)}"


def utc(ts: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """`{{ epoch|utc }}`: a stored epoch as UTC text (pages show UTC; the
    server and the team are in different zones)."""
    return time.strftime(fmt, time.gmtime(ts))


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    """The one Jinja2Templates instance every router renders through.

    Built on first use rather than at import: the environment it configures
    reads settings, and an import-time instance is a module-level global that
    every test then has to work around.
    """
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

    # A global rather than a per-route context value: base.html needs it on
    # every page (it feeds static/js/session.js's idle-expiry timer), and
    # threading it through ~20 routers' TemplateResponse calls would be noise.
    #
    # A callable, not the value: `settings.session` constructs fresh on every
    # access by design (see config/__init__.py), so reading it here would both
    # cache it across test monkeypatches and require SESSION_SECRET to be set
    # merely to build the environment.
    templates.env.globals["session_max_age"] = lambda: (
        settings.session.session_max_age_s
    )
    templates.env.globals["static_url"] = static_url
    # The header, its More menu and the home cards all read schemas.nav.NAV.
    templates.env.globals["nav"] = nav
    templates.env.filters["utc"] = utc
    templates.env.filters["ago"] = ago
    return templates


def render(
    request: Request, name: str, status_code: int = 200, **context: Any
) -> HTMLResponse:
    """Render template `name` as the response, with `context` as its values:
    `render(request, "uniqify.html", gallery=cards, max_copies=50)`."""
    return get_templates().TemplateResponse(
        request, name, context, status_code=status_code
    )


def render_polled(request: Request, name: str, **context: Any) -> Response:
    """`render` for a fragment a page polls (live-gallery.js): an ETag of the
    rendered body, and an empty 304 while it matches the browser's copy. The
    browser revalidates on its own (`no-cache`) and hands the poller its
    cached body, so an unchanged gallery costs a few hundred bytes per poll
    instead of the whole fragment."""
    response = render(request, name, **context)
    etag = f'"{hashlib.sha256(response.body).hexdigest()[:20]}"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return response


def render_partial(name: str, **context: Any) -> str:
    """Render template `name` to a string -- a partial a route hands back
    inside JSON for the page to insert (see /clone/run)."""
    return get_templates().get_template(name).render(**context)
