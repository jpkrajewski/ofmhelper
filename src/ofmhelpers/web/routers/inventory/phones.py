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

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from ofmhelpers.features.phones import service
from ofmhelpers.features.phones import workbook as phones_workbook
from ofmhelpers.features.phones.service import LoginAdded
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import DeviceFields, LoginFields, PhoneQuery
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
    except service.SecretsUnreadableError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


VAULT_REASON = [Depends(_vault_reason)]

router = APIRouter(prefix="/phones", tags=["phones"])


def _phone(device_id: int, scope: Scope) -> service.Phone:
    return service.Phone(scope=scope, device_id=device_id)


def _login(login_id: int, scope: Scope) -> service.SavedLogin:
    return service.SavedLogin(scope=scope, login_id=login_id)


# The signed-in person's phone in the path: not theirs or gone = 404.
Phone = Annotated[service.Phone, Depends(_phone)]
# Their saved login in the path.
Login = Annotated[service.SavedLogin, Depends(_login)]


class _Created(BaseModel):
    id: int


class _Done(BaseModel):
    ok: bool = True


@router.get("")
def index(
    request: Request, query: Annotated[PhoneQuery, Query()], scope: Scope
) -> Response:
    view = service.page_view(query, scope)
    return render(request, "phones.html", query=query, **dict(view))


@router.get("/drawing.svg")
def drawing(model: str = "", color: str = "") -> Response:
    """The phone's back in its color, drawn from the catalog; a plain phone
    for an unknown model. Cached a day: it only changes with a deploy."""
    return Response(
        content=service.drawing(model, color),
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/export.xlsx", dependencies=VAULT_REASON)
def export(scope: Scope, user: CurrentUser) -> Response:
    """Every active phone and its logins, in plaintext (see
    features/phones/workbook.py). Logged: who, and how many -- never a value."""
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


@router.get("/logins/{login_id}/secret", dependencies=VAULT_REASON)
def secret(login_id: int, login: Login, user: CurrentUser) -> Response:
    """One login's password and backup codes, for Show / Copy / Edit.
    Logged: who and which login -- never the value."""
    found = login.secret()
    logger.info("Login %s revealed by %s", login_id, user.email)
    return Response(
        content=found.model_dump_json(),
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
    """Its logins no other phone has leave /accounts and Drive too."""
    phone.delete()
    return _Done()


@router.post("/{device_id}/logins", status_code=201, dependencies=VAULT_REASON)
def add_login(body: LoginFields, phone: Phone) -> LoginAdded:
    return phone.add_login(body)


@router.post("/logins/{login_id}", dependencies=VAULT_REASON)
def edit_login(body: LoginFields, login: Login) -> _Done:
    login.edit(body)
    return _Done()


@router.post("/{device_id}/logins/{login_id}/delete")
def remove_login(login_id: int, phone: Phone) -> _Done:
    """Off its last phone, the account leaves /accounts and Drive too."""
    phone.remove_login(login_id)
    return _Done()


@router.post("/logins/{login_id}/restore")
def restore_login(login: Login) -> _Done:
    """An archived account back on its phones, tracked again."""
    login.restore()
    return _Done()
