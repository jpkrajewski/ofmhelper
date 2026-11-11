"""
/account: the signed-in person's own settings -- how they sign in (Google,
Apple, a password), whether Drive uploads can go up as them (a Google
account linked with Drive), and their own API keys per generation
provider, which their forms pre-fill so their runs bill their account, and
per link provider (/links runs on them; an admin with none on the .env
one). The work: `features/account/service.py`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from ofmhelpers.features.account import service
from ofmhelpers.features.account.service import Notice
from ofmhelpers.features.drive.space import DriveSpace, drive_space
from ofmhelpers.schemas import ApiKeysForm
from ofmhelpers.web.dependencies import CurrentUser
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.responses import NO_STORE, see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/account", tags=["account"])


def _page(request: Request, user_id: int, notice: Notice) -> Response:
    view = service.page_view(user_id, notice)
    # `iter`: the view has a field named `keys`, which `dict(view)` would call.
    response = render(
        request, "account.html", **dict(iter(view)), status_code=notice.status_code
    )
    response.headers.update(NO_STORE)
    return response


@router.get("")
def account_page(
    request: Request, me: CurrentUser, google: str = "", apple: str = ""
) -> Response:
    return _page(request, me.id, service.link_notice(google, apple))


@router.get("/drive-space")
def drive_space_now(me: CurrentUser) -> DriveSpace | None:
    """The signed-in person's Google Drive space, for every page's
    drive-alert.js: `low` (the full pop-up) or `reconnect` (Google refused
    their token: the Reconnect pop-up); null without Google connected or
    when Drive did not answer. Cached (features/drive/space.py): Google is asked
    at most once per TTL per person, whatever it answers."""
    return drive_space(me.id)


@router.post("/password")
def change_password(
    request: Request,
    me: CurrentUser,
    password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    current: Annotated[str, Form()] = "",
) -> Response:
    changed = service.change_password(me.id, current, password, confirm)
    # Every other session of theirs is out now; this one stays in.
    if changed.user is not None:
        AuthMiddleware.remember(request, changed.user)
    return _page(request, me.id, changed.notice)


async def _keys_form(request: Request) -> ApiKeysForm:
    """The keys form: its fields come from data (`key_<provider>` per keyed
    provider; a Remove button posts `remove=<provider>`), so it is read here
    and not as a form model."""
    return service.keys_form(await request.form())


@router.post("/keys")
def save_keys(
    request: Request, me: CurrentUser, form: Annotated[ApiKeysForm, Depends(_keys_form)]
) -> Response:
    """One form, one Save for every provider's key."""
    return _page(request, me.id, service.save_keys(me.id, form))


@router.post("/unlink/{provider}")
def unlink(request: Request, me: CurrentUser, provider: str) -> Response:
    """Stops signing in with that account (and, for Google, Drive uploads
    as it). Refused when it is the only way left to sign in."""
    refused = service.unlink(me.id, provider)
    if refused is not None:
        return _page(request, me.id, refused)
    return see_other("/account")
