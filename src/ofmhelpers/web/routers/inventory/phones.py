"""
/phones: the agency's physical phones -- each a card with its drawing (the
model's back in its color, `ofmhelpers.phones.render`), model, owner and number,
and the logins signed in on it as compact rows (iCloud, Gmail, socials,
any other site). The page carries no password or backup code: Show / Copy /
Edit fetch one login's from `/phones/logins/{id}/secret`. Everyone signed in
views, reveals and downloads `/phones/export.xlsx`; only an admin changes
anything. Filters and sort are the query string (`schemas.PhoneQuery`);
search is instant in the page. Writes post JSON (static/js/phones.js).

Retire takes a phone off the list and the export, every login kept;
Restore brings it back. Delete removes it for good, with the logins no
other phone has.
"""

import datetime
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from ofmhelpers.log import get_logger
from ofmhelpers.phones import catalog, find_model, services
from ofmhelpers.phones.render import render as draw
from ofmhelpers.web import phones_workbook
from ofmhelpers.web.account_tracker import track_login
from ofmhelpers.web.db.encrypted import VaultError
from ofmhelpers.web.db.repositories import DuplicateError
from ofmhelpers.web.middleware.auth import AuthMiddleware
from ofmhelpers.web.schemas import (
    Device,
    DeviceFields,
    LoginFields,
    PhoneQuery,
    PhoneSort,
)
from ofmhelpers.web.stores import devices as store
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)

router = APIRouter(prefix="/phones", tags=["phones"])

AdminOnly = Annotated[None, Depends(AuthMiddleware.require_admin)]

_NO_STORE = {"Cache-Control": "no-store"}


class _Created(BaseModel):
    id: int


class _LoginAdded(BaseModel):
    id: int
    # The account was already saved (on another phone): linked, not copied.
    linked: bool


class _Done(BaseModel):
    ok: bool = True


def _vault[T](action: Callable[..., T], *args: object) -> T:
    """Runs a store call: a duplicate is a 409, a vault problem (missing or
    changed `OFM_VAULT_KEY`) a 500, both with the reason."""
    try:
        return action(*args)
    except DuplicateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except VaultError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _found(ok: bool, what: str) -> _Done:
    if not ok:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return _Done()


def _choices(devices: list[Device]) -> dict[str, list[tuple[str, str]]]:
    """The toolbar's filter options, (value, label): only what some phone
    has, so no option filters to nothing."""
    families = {d.family.id: d.family.label for d in devices if d.family}
    order = [f.id for f in catalog().families]
    colors = sorted({d.finish.label for d in devices if d.finish})
    owners = sorted({d.owner for d in devices if d.owner}, key=str.lower)
    used = {x.service for d in devices for x in d.logins}
    return {
        "family": [(f, families[f]) for f in order if f in families],
        "color": [(c, c) for c in colors],
        "owner": [(o, o) for o in owners],
        "service": [(k, s.label) for k, s in services().items() if k in used],
    }


@router.get("")
def index(request: Request, query: Annotated[PhoneQuery, Query()]):
    devices = store.list_devices()
    active = [d for d in devices if not d.retired]
    shown = query.ordered([d for d in active if query.matches(d)])
    return render(
        request,
        "phones.html",
        devices=shown,
        retired=query.ordered([d for d in devices if d.retired]),
        total=len(active),
        logins=sum(len(d.logins) for d in active),
        choices=_choices(active),
        query=query,
        sorts=PhoneSort,
        default_sort=PhoneQuery().sort,
        services=services(),
        catalog=catalog(),
    )


@router.get("/drawing.svg")
def drawing(model: str = "", color: str = "") -> Response:
    """The phone's back in its color, drawn from the catalog; a plain phone
    for an unknown model. Cached a day: it only changes with a deploy."""
    found = find_model(model)
    phone = found[1] if found else None
    finish = catalog().color(phone, color) if phone else None
    return Response(
        content=draw(phone, finish),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/export.xlsx")
def export(request: Request) -> Response:
    """Every active phone and its logins, in plaintext (see
    web/phones_workbook.py). Logged: who, and how many -- never a value."""
    active = [d for d in _vault(store.export_devices) if not d.retired]
    logins = sum(len(d.logins) for d in active)
    logger.info(
        "Phones export by %s: %s phones, %s logins",
        request.session.get("role"),
        len(active),
        logins,
    )
    today = datetime.datetime.now(tz=datetime.UTC).date()
    name = f"phones-{today.isoformat()}.xlsx"
    return Response(
        content=phones_workbook.build(active),
        media_type=phones_workbook.MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{name}"', **_NO_STORE},
    )


@router.get("/logins/{login_id}/secret")
def secret(request: Request, login_id: int) -> Response:
    """One login's password and backup codes, for Show / Copy / Edit.
    Logged: who and which login -- never the value."""
    found = _vault(store.secrets, login_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Login not found")
    logger.info("Login %s revealed by %s", login_id, request.session.get("role"))
    return Response(
        content=found.model_dump_json(),
        media_type="application/json",
        headers=_NO_STORE,
    )


@router.post("/add")
def add(body: DeviceFields, _admin: AdminOnly) -> _Created:
    return _Created(id=_vault(store.add_device, body))


@router.post("/{device_id}")
def edit(device_id: int, body: DeviceFields, _admin: AdminOnly) -> _Done:
    return _found(_vault(store.update_device, device_id, body), "Phone")


@router.post("/{device_id}/retire")
def retire(device_id: int, _admin: AdminOnly) -> _Done:
    return _found(store.retire_device(device_id), "Phone")


@router.post("/{device_id}/restore")
def restore(device_id: int, _admin: AdminOnly) -> _Done:
    return _found(store.restore_device(device_id), "Phone")


@router.post("/{device_id}/delete")
def delete(device_id: int, _admin: AdminOnly) -> _Done:
    return _found(store.delete_device(device_id), "Phone")


@router.post("/{device_id}/logins")
def add_login(device_id: int, body: LoginFields, _admin: AdminOnly) -> _LoginAdded:
    added = _vault(store.add_login, device_id, body)
    if added is None:
        raise HTTPException(status_code=404, detail="Phone not found")
    # An Instagram (any tracked service's) login: on /accounts with this phone.
    track_login(body.service, body.username, revive=True)
    return _LoginAdded(id=added[0], linked=added[1])


@router.post("/logins/{login_id}")
def edit_login(login_id: int, body: LoginFields, _admin: AdminOnly) -> _Done:
    done = _found(_vault(store.update_login, login_id, body), "Login")
    # A renamed handle is tracked; an account removed by hand stays removed
    # (editing a password is no reason to bring it back).
    track_login(body.service, body.username, revive=False)
    return done


@router.post("/{device_id}/logins/{login_id}/delete")
def remove_login(device_id: int, login_id: int, _admin: AdminOnly) -> _Done:
    return _found(store.remove_login(device_id, login_id), "Login")
