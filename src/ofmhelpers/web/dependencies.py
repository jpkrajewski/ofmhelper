from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request, WebSocket
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
    what: Area | Action

    async def __call__(self, request: Request) -> None:
        AuthMiddleware.require(request, self.what)


def Access(area: Area) -> Any:  # noqa: N802
    return Depends(Allowed(what=area))


def Can(action: Action) -> Any:  # noqa: N802
    return Depends(Allowed(what=action))


def _assets_root(request: Request) -> Path:
    from ofmhelpers.web.routers.task_helpers import assets_root_for

    return assets_root_for(request)


def _signed_in(websocket: WebSocket) -> Callable[[], Viewer | None]:
    return partial(AuthMiddleware.socket_viewer, websocket)


def _api_keys(request: Request) -> dict[str, str]:
    return get_api_keys(request)


Viewing = Annotated[Viewer, Depends(_viewer)]
CurrentUser = Annotated[SessionUser, Depends(_current_user)]
SignedIn = Annotated[Callable[[], Viewer | None], Depends(_signed_in)]
Actor = Annotated[str, Depends(_actor)]
Actors = Annotated[frozenset[str], Depends(_actors)]
OwnerId = Annotated[int, Depends(_owner_id)]
Scope = Annotated[int, Depends(_scope)]
AssetsRoot = Annotated[Path, Depends(_assets_root)]
ApiKeys = Annotated[dict[str, str], Depends(_api_keys)]
