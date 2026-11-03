"""
/phones: physical phones -- each a card with its drawing (the model's back
in its color, `ofmhelpers.features.phones.catalog.render`), model, owner and number, and the
logins signed in on it as compact rows (iCloud, Gmail, socials, any other
site). The page carries no password or backup code: Show / Copy / Edit
fetch one login's from `/phones/logins/{id}/secret`. Each phone belongs to a
user: they see, reveal, export and change only theirs -- admins too,
nobody sees another person's phones. A phone added is the adder's. Filters and sort are the query string (`schemas.PhoneQuery`);
search is instant in the page. Writes post JSON (static/js/phones.js).

Retire takes a phone off the list and the export, every login kept;
Restore brings it back. Delete removes it for good, with the logins no
other phone has.
"""

import datetime
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.stores import devices as store
from ofmhelpers.features.accounts.removal import logins_leaving
from ofmhelpers.features.accounts.tracker import track_login
from ofmhelpers.features.phones import workbook as phones_workbook
from ofmhelpers.features.phones.catalog import catalog, find_model, services
from ofmhelpers.features.phones.catalog.render import render as draw
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import DeviceFields, LoginFields, PhoneQuery, PhoneSort
from ofmhelpers.web.dependencies import CurrentUser, OwnerId, Scope
from ofmhelpers.web.responses import NO_STORE
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)


def _vault_reason() -> Iterator[None]:
    """A vault problem (missing or changed `OFM_VAULT_KEY`) is a 500 with the
    reason. Here and not in `REFUSALS`: only /phones may show it. Only on the
    routes that read or write a secret on purpose, not on the whole router: the
    list page must never answer a person with raw JSON."""
    try:
        yield
    except VaultError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


VAULT_REASON = [Depends(_vault_reason)]

router = APIRouter(prefix="/phones", tags=["phones"])


class _Created(BaseModel):
    id: int


class _LoginAdded(BaseModel):
    id: int
    # The account was already saved (on another phone): linked, not copied.
    linked: bool


class _Done(BaseModel):
    ok: bool = True


def _found(ok: bool, what: str) -> _Done:
    if not ok:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return _Done()


@router.get("")
def index(
    request: Request, query: Annotated[PhoneQuery, Query()], scope: Scope
) -> Response:
    devices = store.list_devices(scope)
    active = [d for d in devices if not d.retired]
    shown = query.ordered([d for d in active if query.matches(d)])
    return render(
        request,
        "phones.html",
        devices=shown,
        retired=query.ordered([d for d in devices if d.retired]),
        archived=store.archived_logins(scope),
        total=len(active),
        logins=sum(len(d.logins) for d in active),
        # The add/edit dialog suggests the people already holding a phone.
        owners=sorted({d.owner for d in active if d.owner}, key=str.lower),
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


@router.get("/export.xlsx", dependencies=VAULT_REASON)
def export(scope: Scope, user: CurrentUser) -> Response:
    """Every active phone and its logins, in plaintext (see
    features/phones/workbook.py). Logged: who, and how many -- never a value."""
    active = [d for d in store.export_devices(scope) if not d.retired]
    logins = sum(len(d.logins) for d in active)
    logger.info(
        "Phones export by %s: %s phones, %s logins",
        user.email,
        len(active),
        logins,
    )
    today = datetime.datetime.now(tz=datetime.UTC).date()
    name = f"phones-{today.isoformat()}.xlsx"
    return Response(
        content=phones_workbook.build(active),
        media_type=phones_workbook.MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{name}"', **NO_STORE},
    )


@router.get("/logins/{login_id}/secret", dependencies=VAULT_REASON)
def secret(login_id: int, scope: Scope, user: CurrentUser) -> Response:
    """One login's password and backup codes, for Show / Copy / Edit.
    Logged: who and which login -- never the value."""
    found = store.secrets(login_id, scope)
    if found is None:
        raise HTTPException(status_code=404, detail="Login not found")
    logger.info("Login %s revealed by %s", login_id, user.email)
    return Response(
        content=found.model_dump_json(),
        media_type="application/json",
        headers=NO_STORE,
    )


@router.post("/add", dependencies=VAULT_REASON)
def add(body: DeviceFields, owner_id: OwnerId) -> _Created:
    return _Created(id=store.add_device(body, owner_id))


@router.post("/{device_id}", dependencies=VAULT_REASON)
def edit(device_id: int, body: DeviceFields, scope: Scope) -> _Done:
    return _found(store.update_device(device_id, body, scope), "Phone")


@router.post("/{device_id}/retire")
def retire(device_id: int, scope: Scope) -> _Done:
    return _found(store.retire_device(device_id, scope), "Phone")


@router.post("/{device_id}/restore")
def restore(device_id: int, scope: Scope) -> _Done:
    return _found(store.restore_device(device_id, scope), "Phone")


@router.post("/{device_id}/delete")
def delete(device_id: int, scope: Scope) -> _Done:
    """Its logins no other phone has leave /accounts and Drive too."""
    logins_leaving(scope, device_id)
    return _found(store.delete_device(device_id, scope), "Phone")


def _track(login_id: int, body: LoginFields, *, revive: bool) -> None:
    """An Instagram (any tracked service's) login shows on its phone owner's
    /accounts."""
    owner = store.owner_of_login(login_id)
    if owner is not None:
        track_login(body.service, body.username, user_id=owner, revive=revive)


@router.post("/{device_id}/logins", dependencies=VAULT_REASON)
def add_login(device_id: int, body: LoginFields, scope: Scope) -> _LoginAdded:
    added = store.add_login(device_id, body, scope)
    if added is None:
        raise HTTPException(status_code=404, detail="Phone not found")
    _track(added[0], body, revive=True)
    return _LoginAdded(id=added[0], linked=added[1])


@router.post("/logins/{login_id}", dependencies=VAULT_REASON)
def edit_login(login_id: int, body: LoginFields, scope: Scope) -> _Done:
    done = _found(store.update_login(login_id, body, scope), "Login")
    # A renamed handle is tracked; an account removed by hand stays removed
    # (editing a password is no reason to bring it back).
    _track(login_id, body, revive=False)
    return done


@router.post("/{device_id}/logins/{login_id}/delete")
def remove_login(device_id: int, login_id: int, scope: Scope) -> _Done:
    """Off its last phone, the account leaves /accounts and Drive too."""
    logins_leaving(scope, device_id, login_id)
    return _found(store.remove_login(device_id, login_id, scope), "Login")


@router.post("/logins/{login_id}/restore")
def restore_login(login_id: int, scope: Scope) -> _Done:
    """An archived account back on its phones, tracked again (its Drive
    folder is made at the next sweep). Not one whose phones were all
    deleted meanwhile: restored, it would sit on no phone and the next
    phone change would delete it, password included."""
    archived = {x.id: x for x in store.archived_logins(scope)}
    if login_id in archived and not archived[login_id].on:
        msg = "No phone has this account any more: add it to a phone instead"
        raise HTTPException(status_code=409, detail=msg)
    found = store.restore_login(login_id, scope)
    if found is None:
        raise HTTPException(status_code=404, detail="Archived login not found")
    owner = store.owner_of_login(login_id)
    if owner is not None:
        track_login(found.service, found.username, user_id=owner, revive=True)
    return _Done()
