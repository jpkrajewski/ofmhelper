import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ofmhelpers.config import settings

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
    return templates


def render(
    request: Request, name: str, status_code: int = 200, **context: Any
) -> HTMLResponse:
    """Render template `name` as the response, with `context` as its values:
    `render(request, "uniqify.html", gallery=cards, max_copies=50)`."""
    return get_templates().TemplateResponse(
        request, name, context, status_code=status_code
    )


def render_partial(name: str, **context: Any) -> str:
    """Render template `name` to a string -- a partial a route hands back
    inside JSON for the page to insert (see /clone/run)."""
    return get_templates().get_template(name).render(**context)
