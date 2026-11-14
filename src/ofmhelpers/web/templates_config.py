import hashlib
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
lazy from fastapi import Request

from ofmhelpers.auth.access import Action, Area, allowed
from ofmhelpers.config import settings
from ofmhelpers.features.drive.posting import window as posting_window
from ofmhelpers.features.users.presence import idle_limit_s
from ofmhelpers.features.users.viewing import makers
from ofmhelpers.schemas.accounts import ago
from ofmhelpers.schemas.nav import nav
from ofmhelpers.schemas.time_zones import TIME_ZONES, time_zone
lazy from ofmhelpers.features.users.viewing import Maker
lazy from ofmhelpers.schemas.users import Viewer

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATES_DIR = Path(__file__).parent / "templates"
# Outside production, how often a template's file is looked at for an edit.
TEMPLATE_RECHECK_S = 1.0


class TemplateFiles(FileSystemLoader):
    """Template edits picked up without a restart, at a fraction of Jinja's
    cost: it checks a template's file on every include/import -- ~5 per
    gallery card, ~2 ms each on dev's Windows bind mount, 200 ms of a
    20-card render that takes 4 ms without (2026-10-24). This looks at each
    file once every TEMPLATE_RECHECK_S at most. Production compiles every
    template once and never looks (its templates are in the image)."""

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


def _viewer_of(request: Request) -> Viewer | None:
    """The request's viewer (`middleware/auth.py`); None on a public page."""
    return getattr(request.state, "viewer", None)


def _session_max_age(request: Request) -> int:
    """Seconds the page's tab may sit idle before session.js signs it out:
    the limit of who is signed in (a VA's is shorter; an admin viewing as
    someone keeps their own)."""
    viewer = _viewer_of(request)
    signed_in = (viewer.by or viewer.user) if viewer is not None else None
    return idle_limit_s(signed_in.role if signed_in is not None else None)


def _signed_in_at(request: Request) -> int | None:
    """When the request's session signed in (whole epoch seconds); None for
    none."""
    since = request.session.get("since") if "session" in request.scope else None
    return int(since) if since else None


def _makers(request: Request) -> list[Maker]:
    """The choices of a gallery's "VAs" filter for the page's reader."""
    viewer = _viewer_of(request)
    return makers(viewer) if viewer is not None else []


def _can(request: Request, what: str) -> bool:
    """Whether the page's reader may do an `Action` or open an `Area`, by
    its value (`can(request, "drive-upload")`). While an admin views as
    someone nothing can be changed, so no action is offered."""
    viewer = _viewer_of(request)
    if viewer is None:
        return False
    if what in Action:
        return not viewer.read_only and allowed(viewer.user.role, Action(what))
    return allowed(viewer.user.role, Area(what))


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    """The one Jinja2Templates instance every router renders through.

    Built on first use rather than at import: the environment it configures
    reads settings, and an import-time instance is a module-level global that
    every test then has to work around.
    """
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    templates.env.loader = TemplateFiles(TEMPLATES_DIR)
    templates.env.auto_reload = not settings.infra.production

    # A global rather than a per-route context value: base.html needs it on
    # every page (it feeds static/js/session.js's idle-expiry timer), and
    # threading it through ~20 routers' TemplateResponse calls would be noise.
    #
    # A callable, not the value: `settings.session` constructs fresh on every
    # access by design (see config/__init__.py), so reading it here would both
    # cache it across test monkeypatches and require SESSION_SECRET to be set
    # merely to build the environment.
    templates.env.globals["session_max_age"] = _session_max_age
    templates.env.globals["static_url"] = static_url
    # The header, its More menu and the home cards all read schemas.nav.NAV.
    templates.env.globals["nav"] = nav
    # Who the page is rendered for (None on a public page) and what they may
    # do: templates never read the session themselves.
    templates.env.globals["viewer_of"] = _viewer_of
    templates.env.globals["can"] = _can
    templates.env.globals["makers_of"] = _makers
    templates.env.globals["time_zones"] = TIME_ZONES
    templates.env.globals["time_zone"] = time_zone
    # When this session signed in: a page's tour starts by itself once per
    # sign-in (static/js/tour.js).
    templates.env.globals["signed_in_at"] = _signed_in_at
    # The daily posting window: base.html's "Start posting content".
    templates.env.globals["posting_window"] = posting_window
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
