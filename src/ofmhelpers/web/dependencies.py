"""The FastAPI dependencies routers share: who is asking, which part of the
app's data a request works on, and whether they may.

Policy stays on `AuthMiddleware` (web/middleware/auth.py: the one place for
anything auth-shaped), which builds the request's `Viewer` once; these hand
its answers to a route as typed parameters, so a route declares
`actor: Actor` instead of calling `AuthMiddleware.actor(request)` itself, and
what it passes on to a store or service is a plain value -- never the
request. Each is also the seam a test overrides (`app.dependency_overrides`).

Who may open what is `auth/access.py`, asked here: every router names its
area (`APIRouter(dependencies=[Access(Area.CLONE)])`), a route fewer roles
may use adds `dependencies=[Can(Action.DRIVE_UPLOAD)]`.
`tests/unit/architecture/` fails on a route with neither a public path nor
an area.

Reading the request's viewer is in-memory work, so those are `async def`
(no threadpool hop); the store and the saved API keys are read from
Postgres, so those are plain `def` and run in the threadpool.
"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel

from ofmhelpers.auth.access import Action, Area
from ofmhelpers.schemas import SessionUser
from ofmhelpers.schemas.users import Viewer
from ofmhelpers.web.api_keys import get_api_keys
from ofmhelpers.web.middleware import AuthMiddleware


async def _viewer(request: Request) -> Viewer:
    return AuthMiddleware.viewer(request)


async def _current_user(request: Request) -> SessionUser:
    return AuthMiddleware.user(request)


async def _actor(request: Request) -> str:
    return AuthMiddleware.actor(request)


async def _actors(request: Request) -> frozenset[str]:
    return AuthMiddleware.viewer(request).actors


async def _owner_id(request: Request) -> int:
    return AuthMiddleware.owner_id(request)


async def _scope(request: Request) -> int:
    return AuthMiddleware.scope(request)


class Allowed(BaseModel, frozen=True):
    """The guard behind `Access` and `Can`: 403 unless the access map lets
    the request's role open `what`. An object, so a test can read which area
    a route names (`route.dependant`)."""

    what: Area | Action

    async def __call__(self, request: Request) -> None:
        AuthMiddleware.require(request, self.what)


def Access(area: Area) -> Any:  # noqa: N802 -- reads as the dependency it is
    """Router-level: `APIRouter(dependencies=[Access(Area.CLONE)])`."""
    return Depends(Allowed(what=area))


def Can(action: Action) -> Any:  # noqa: N802
    """Route-level, for one thing fewer roles may do inside an area."""
    return Depends(Allowed(what=action))


def _assets_root(request: Request) -> Path:
    # Imported here, not at the top: the asset store still lives in the
    # routers package, whose __init__ imports every router -- and the routers
    # import this module.
    from ofmhelpers.web.routers.task_helpers import assets_root_for

    return assets_root_for(request)


def _api_keys(request: Request) -> dict[str, str]:
    return get_api_keys(request)


# Who the request acts as, whole: for a feature that needs more than one of
# the values below.
Viewing = Annotated[Viewer, Depends(_viewer)]
# The person the request acts as (the middleware let it through, so there is
# one): the one signed in, or the one an admin is viewing as.
CurrentUser = Annotated[SessionUser, Depends(_current_user)]
# What a job this request starts is stored under: the user's id as a string.
Actor = Annotated[str, Depends(_actor)]
# Whose jobs this request may read: their own and their VAs'.
Actors = Annotated[frozenset[str], Depends(_actors)]
# Who a row this request creates belongs to.
OwnerId = Annotated[int, Depends(_owner_id)]
# Whose rows this request may read and change: always the user's own.
Scope = Annotated[int, Depends(_scope)]
# This session's part of the asset store (a VA's: their owner's).
AssetsRoot = Annotated[Path, Depends(_assets_root)]
# The provider API-key pre-fills of a generation form.
ApiKeys = Annotated[dict[str, str], Depends(_api_keys)]
