# Copyright (C) 2026 jpkrajewski

from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, cast

from fastapi import Cookie, Depends, Request, WebSocket, params
from pydantic import BaseModel

from ofmhelpers.db.users.schema import SessionUser, Viewer
from ofmhelpers.features.assets.store import assets_root_for_user
from ofmhelpers.features.downloads.service import DownloadService
from ofmhelpers.features.notifications.schema import Reader
from ofmhelpers.features.storage.client import access, storage_owner
from ofmhelpers.features.storage.models import StorageAccess
from ofmhelpers.features.storage.service import StorageService
from ofmhelpers.web.access import Action, Area, opens
from ofmhelpers.web.api_keys import get_api_keys
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.time_zones import TimeZone, time_zone


async def _viewer(request: Request) -> Viewer:
    return AuthMiddleware.viewer(request)


async def _current_user(request: Request) -> SessionUser:
    return AuthMiddleware.user(request)


async def _session_id(request: Request) -> str:
    return AuthMiddleware.session_id(request)


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


def Access(area: Area) -> params.Depends:  # noqa: N802
    return cast("params.Depends", Depends(Allowed(what=area)))


def Can(action: Action) -> params.Depends:  # noqa: N802
    return cast("params.Depends", Depends(Allowed(what=action)))


def _assets_root(request: Request) -> Path:
    return assets_root_for_user(AuthMiddleware.user(request).id)


def _reader(websocket: WebSocket) -> Reader | None:
    viewer = AuthMiddleware.socket_viewer(websocket)
    if viewer is None:
        return None
    return Reader(user_id=viewer.user.id, chats=opens(viewer, Area.CHATTING))


def _signed_in(websocket: WebSocket) -> Callable[[], Reader | None]:
    return partial(_reader, websocket)


def _api_keys(request: Request) -> MappingProxyType[str, str]:
    return get_api_keys(request)


Viewing = Annotated[Viewer, Depends(_viewer)]
CurrentUser = Annotated[SessionUser, Depends(_current_user)]
SignedIn = Annotated[Callable[[], Reader | None], Depends(_signed_in)]
SessionId = Annotated[str, Depends(_session_id)]
Actor = Annotated[str, Depends(_actor)]
Actors = Annotated[frozenset[str], Depends(_actors)]
OwnerId = Annotated[int, Depends(_owner_id)]
Scope = Annotated[int, Depends(_scope)]
AssetsRoot = Annotated[Path, Depends(_assets_root)]


def _storage_access(scope: Scope) -> StorageAccess:
    return access(scope, interactive=True)


StorageProvider = Annotated[StorageAccess, Depends(_storage_access)]


def _storage(provider: StorageProvider) -> StorageService:
    return StorageService.compose(provider)


def _employer_storage(owner_id: OwnerId) -> StorageService:
    return StorageService.compose(access(storage_owner(owner_id), interactive=True))


Storage = Annotated[StorageService, Depends(_storage)]
EmployerStorage = Annotated[StorageService, Depends(_employer_storage)]


def _downloads(
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
    storage: EmployerStorage,
) -> DownloadService:
    return DownloadService.compose(actor, owner_id, assets_root, storage)


Downloads = Annotated[DownloadService, Depends(_downloads)]


def _chosen_zone(ofm_timezone: Annotated[str | None, Cookie()] = None) -> TimeZone:
    return time_zone(ofm_timezone)


Zone = Annotated[TimeZone, Depends(_chosen_zone)]
ApiKeys = Annotated[MappingProxyType[str, str], Depends(_api_keys)]
