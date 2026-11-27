# Copyright (C) 2026 jpkrajewski

import logging
from typing import Annotated
lazy from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from ofmhelpers.db.devices.models import DeviceFields, LoginFields, PhoneQuery
from ofmhelpers.db.devices.schema import AssignAccount
from ofmhelpers.db.phone_revisions.schema import Restored
from ofmhelpers.features.phones import service
from ofmhelpers.features.phones import workbook as phones_workbook
from ofmhelpers.features.phones.revisions import Revisions
from ofmhelpers.features.phones.service import LoginAdded
from ofmhelpers.utils.secrets.json_dump import model_json_dump
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import (
    Access,
    CurrentUser,
    OwnerId,
    Scope,
    Storage,
    Viewing,
)
from ofmhelpers.web.responses import NO_STORE
from ofmhelpers.web.templates_config import render

logger = logging.getLogger(__name__)


def _vault_reason() -> Iterator[None]:
    try:
        yield
    except service.SecretsUnreadableError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


VAULT_REASON = [Depends(_vault_reason)]


def _own_eyes(viewer: Viewing) -> None:
    if viewer.by is not None:
        raise HTTPException(
            status_code=403,
            detail="Passwords are shown only to their owner",
        )


SECRET = [Depends(_own_eyes), *VAULT_REASON]

router = APIRouter(
    prefix="/phones",
    tags=["phones"],
    dependencies=[Access(Area.PHONES)],
)


def _phone(device_id: int, scope: Scope, storage: Storage) -> service.Phone:
    return service.Phone(scope=scope, device_id=device_id, folders=storage.folders)


def _login(login_id: int, scope: Scope) -> service.SavedLogin:
    return service.SavedLogin(scope=scope, login_id=login_id)


Phone = Annotated[service.Phone, Depends(_phone)]
Login = Annotated[service.SavedLogin, Depends(_login)]


class _Created(BaseModel):
    id: int


class _Done(BaseModel):
    ok: bool = True


@router.get("")
def index(
    request: Request,
    query: Annotated[PhoneQuery, Query()],
    scope: Scope,
) -> Response:
    view = service.page_view(query, scope)
    return render(request, "phones.html", {"query": query, **dict(view)})


@router.get("/drawing.svg")
def drawing(model: str = "", color: str = "") -> Response:
    return Response(
        content=service.drawing(model, color),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/revisions", summary="History: the phones after each change")
def revisions(request: Request, scope: Scope) -> Response:
    return render(
        request,
        "_phone_revisions.html",
        {"revisions": Revisions(scope=scope).listed()},
    )


@router.post(
    "/revisions/{revision_id}/restoration",
    status_code=201,
    summary="Go back to the phones of a revision",
)
def restore_revision(revision_id: int, scope: Scope, user: CurrentUser) -> Restored:
    done = Revisions(scope=scope).restore(revision_id)
    logger.info("Phones restored to revision %s by %s", revision_id, user.email)
    return done


@router.get("/export.xlsx", dependencies=SECRET)
def export(scope: Scope, user: CurrentUser) -> Response:
    book = service.export(scope)
    logger.info(
        "Phones export by %s: %s phones, %s logins",
        user.email,
        book.phones,
        book.logins,
    )
    disposition = f'attachment; filename="{book.name}"'
    return Response(
        content=book.content,
        media_type=phones_workbook.MEDIA_TYPE,
        headers={"Content-Disposition": disposition, **NO_STORE},
    )


@router.get("/logins/{login_id}/secret", dependencies=SECRET)
def secret(login_id: int, login: Login, user: CurrentUser) -> Response:
    found = login.secret()
    return Response(
        content=model_json_dump(found, f"Login {login_id} revealed by {user.email}"),
        media_type="application/json",
        headers=NO_STORE,
    )


@router.post("/add", status_code=201, dependencies=VAULT_REASON)
def add(body: DeviceFields, owner_id: OwnerId) -> _Created:
    return _Created(id=service.add(body, owner_id))


@router.post("/{device_id}", dependencies=VAULT_REASON)
def edit(body: DeviceFields, phone: Phone) -> _Done:
    phone.edit(body)
    return _Done()


@router.post("/{device_id}/retire")
def retire(phone: Phone) -> _Done:
    phone.retire()
    return _Done()


@router.post("/{device_id}/restore")
def restore(phone: Phone) -> _Done:
    phone.restore()
    return _Done()


@router.post("/{device_id}/delete")
def delete(phone: Phone) -> _Done:
    phone.delete()
    return _Done()


@router.post("/{device_id}/logins", status_code=201, dependencies=VAULT_REASON)
def add_login(body: LoginFields, phone: Phone) -> LoginAdded:
    return phone.add_login(body)


@router.post("/{device_id}/accounts", status_code=201, dependencies=VAULT_REASON)
def assign_account(body: AssignAccount, phone: Phone) -> LoginAdded:
    return phone.assign(body)


@router.post("/logins/{login_id}", dependencies=VAULT_REASON)
def edit_login(body: LoginFields, login: Login) -> _Done:
    login.edit(body)
    return _Done()


@router.post("/{device_id}/logins/{login_id}/delete")
def remove_login(login_id: int, phone: Phone) -> _Done:
    phone.remove_login(login_id)
    return _Done()


@router.post("/logins/{login_id}/restore")
def restore_login(login: Login) -> _Done:
    login.restore()
    return _Done()
