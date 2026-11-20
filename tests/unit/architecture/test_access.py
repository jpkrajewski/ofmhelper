import re

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts

from ofmhelpers.web.access import ACTIONS, RETIRED, Action, Area
from ofmhelpers.web.dependencies import Allowed
from ofmhelpers.web.main import app
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.nav import NAV

PATH_PARAMETER = re.compile(r"\{[^}]*\}")


def api_routes() -> list[RouteContext]:
    found = [
        route
        for route in iter_route_contexts(app.routes)
        if isinstance(route.original_route, APIRoute)
    ]
    assert found, "no routes read off the app"
    return found


def guards(dependant: Dependant) -> set[Area | Action]:
    found: set[Area | Action] = set()
    for dependency in dependant.dependencies:
        if isinstance(dependency.call, Allowed):
            found.add(dependency.call.what)
        found |= guards(dependency)
    return found


def areas(route: RouteContext) -> set[Area]:
    return {what for what in guards(route.dependant) if isinstance(what, Area)}


def is_public(route: RouteContext) -> bool:
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
        if item.area not in RETIRED | pages.get(item.href, set())
    )
    assert not wrong, "menu entries whose page is another area's:\n" + "\n".join(wrong)

    used = {area for route in api_routes() for area in areas(route)}
    unused = sorted(set(Area) - used - RETIRED)
    assert not unused, f"areas no route names: {unused}"


def test_every_action_has_a_route():
    used = {what for route in api_routes() for what in guards(route.dependant)}
    unused = sorted(set(ACTIONS) - used)
    assert not unused, f"actions no route names: {unused}"
