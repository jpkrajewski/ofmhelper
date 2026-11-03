"""The FastAPI dependencies routers share: who is asking, and which part of
the app's data a request works on.

Policy stays on `AuthMiddleware` (web/middleware/auth.py: the one place for
anything auth-shaped); these hand its answers to a route as typed
parameters, so a route declares `actor: Actor` instead of calling
`AuthMiddleware.actor(request)` itself, and what it passes on to a store or
service is a plain value -- never the request. Each is also the seam a test
overrides (`app.dependency_overrides`).

Reading the signed session cookie is in-memory work, so those are
`async def` (no threadpool hop); the store and the saved API keys are read
from Postgres, so those are plain `def` and run in the threadpool.
"""

from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

from ofmhelpers.schemas import SessionUser
from ofmhelpers.web.api_keys import get_api_keys
from ofmhelpers.web.middleware import AuthMiddleware


async def _current_user(request: Request) -> SessionUser:
    return AuthMiddleware.user(request)


async def _actor(request: Request) -> str:
    return AuthMiddleware.actor(request)


async def _owner_id(request: Request) -> int:
    return AuthMiddleware.owner_id(request)


async def _scope(request: Request) -> int:
    return AuthMiddleware.scope(request)


async def _require_admin(request: Request) -> None:
    AuthMiddleware.require_admin(request)


def _assets_root(request: Request) -> Path:
    # Imported here, not at the top: the asset store still lives in the
    # routers package, whose __init__ imports every router -- and the routers
    # import this module.
    from ofmhelpers.web.routers.task_helpers import assets_root_for

    return assets_root_for(request)


def _api_keys(request: Request) -> dict[str, str]:
    return get_api_keys(request)


# The signed-in user (the middleware let the request through, so there is one).
CurrentUser = Annotated[SessionUser, Depends(_current_user)]
# What a job this request starts is stored under: the user's id as a string.
Actor = Annotated[str, Depends(_actor)]
# Who a row this request creates belongs to.
OwnerId = Annotated[int, Depends(_owner_id)]
# Whose rows this request may read and change: always the user's own.
Scope = Annotated[int, Depends(_scope)]
# This session's part of the asset store.
AssetsRoot = Annotated[Path, Depends(_assets_root)]
# The provider API-key pre-fills of a generation form.
ApiKeys = Annotated[dict[str, str], Depends(_api_keys)]
# A router-level `dependencies=[AdminOnly]`: 403 for anyone not an admin.
AdminOnly = Depends(_require_admin)
