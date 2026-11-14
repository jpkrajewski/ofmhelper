"""Who may open what, as checks on the app's own routes.

Every route is either public on purpose (`settings.web.public_paths` /
`public_prefixes`) or names an area of the access map (`auth/access.py`):
`APIRouter(dependencies=[Access(Area.X)])`, read here off each route's
dependency tree (`web/dependencies.py` `Allowed`). No baseline: a route with
neither fails, so does an area or an action no route uses, and a menu entry
whose page is another area's.
"""

import os
import re

os.environ.setdefault("SESSION_SECRET", "test-secret")

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from ofmhelpers.auth.access import ACTIONS, Action, Area
from ofmhelpers.schemas.nav import NAV
from ofmhelpers.web.dependencies import Allowed
from ofmhelpers.web.main import app
from ofmhelpers.web.middleware import AuthMiddleware

PATH_PARAMETER = re.compile(r"\{[^}]*\}")


def api_routes() -> list[RouteContext]:
    """Every route of the app as it is served: under its router's prefix,
    with its router's dependencies (FastAPI keeps an included router as one
    entry of `app.routes`, so they are read through its own iterator)."""
    found = [
        route
        for route in iter_route_contexts(app.routes)
        if isinstance(route.original_route, APIRoute)
    ]
    assert found, "no routes read off the app"
    return found


def guards(dependant: Dependant) -> set[Area | Action]:
    """What the route's `Access` / `Can` dependencies name, nested ones too."""
    found: set[Area | Action] = set()
    for dependency in dependant.dependencies:
        if isinstance(dependency.call, Allowed):
            found.add(dependency.call.what)
        found |= guards(dependency)
    return found


def areas(route: RouteContext) -> set[Area]:
    return {what for what in guards(route.dependant) if isinstance(what, Area)}


def is_public(route: RouteContext) -> bool:
    """The route's path with a sample value in each parameter, asked of the
    middleware's own allowlist."""
    return AuthMiddleware.is_public(PATH_PARAMETER.sub("x", route.path or ""))


def test_every_route_is_public_or_names_its_area():
    unguarded = sorted(
        f"{method} {route.path}"
        for route in api_routes()
        if not is_public(route) and not areas(route)
        for method in route.methods or ()
    )
    assert not unguarded, (
        "routes with no area (APIRouter(dependencies=[Access(Area.X)])) "
        "and no public path:\n" + "\n".join(unguarded)
    )


def test_every_area_has_a_route_and_the_menu_names_its_pages_area():
    pages = {
        route.path: areas(route)
        for route in api_routes()
        if "GET" in (route.methods or ())
    }
    wrong = sorted(
        f"{item.href}: menu says {item.area}, its route {sorted(pages.get(item.href, []))}"
        for item in NAV
        if item.area not in pages.get(item.href, set())
    )
    assert not wrong, "menu entries whose page is another area's:\n" + "\n".join(wrong)

    used = {area for route in api_routes() for area in areas(route)}
    unused = sorted(set(Area) - used)
    assert not unused, f"areas no route names: {unused}"


def test_every_action_has_a_route():
    used = {what for route in api_routes() for what in guards(route.dependant)}
    unused = sorted(set(ACTIONS) - used)
    assert not unused, f"actions no route names: {unused}"
