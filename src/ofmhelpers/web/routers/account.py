from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from ofmhelpers.db.users.schema import ApiKeysForm, SessionUser
from ofmhelpers.features.account import service
from ofmhelpers.features.account.service import Notice
from ofmhelpers.features.storage.schema import DriveSpace
from ofmhelpers.web.access import Action, Area, allowed
from ofmhelpers.web.dependencies import Access, Can, CurrentUser, Storage
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.responses import NO_STORE, see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/account", tags=["account"], dependencies=[Access(Area.ACCOUNT)]
)


def _chats(me: SessionUser) -> bool:
    return allowed(me.role, Area.CHATTING)


def _page(request: Request, me: SessionUser, notice: Notice) -> Response:
    view = service.page_view(me.id, notice, chats=_chats(me))
    response = render(
        request, "account.html", dict(iter(view)), status_code=notice.status_code
    )
    response.headers.update(NO_STORE)
    return response


@router.get("")
def account_page(
    request: Request, me: CurrentUser, google: str = "", apple: str = ""
) -> Response:
    return _page(request, me, service.link_notice(google, apple))


@router.get("/drive-space")
def drive_space_now(storage: Storage) -> DriveSpace | None:
    return storage.space.drive_space()


@router.post("/password", dependencies=[Can(Action.CHANGE_PASSWORD)])
def change_password(
    request: Request,
    me: CurrentUser,
    password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    current: Annotated[str, Form()] = "",
) -> Response:
    changed = service.change_password(me.id, current, password, confirm)
    if changed.user is not None:
        AuthMiddleware.remember(request, changed.user)
    return _page(request, me, changed.notice)


async def _keys_form(request: Request) -> ApiKeysForm:
    return service.keys_form(await request.form())


@router.post("/keys", dependencies=[Can(Action.SAVE_KEYS)])
def save_keys(
    request: Request, me: CurrentUser, form: Annotated[ApiKeysForm, Depends(_keys_form)]
) -> Response:
    return _page(request, me, service.save_keys(me.id, form, chats=_chats(me)))


@router.post("/telegram")
def connect_telegram(request: Request, me: CurrentUser) -> Response:
    found = service.connect_telegram(me.id)
    if found.url is not None:
        return see_other(found.url)
    return _page(request, me, found.notice)


@router.post("/telegram/delete")
def disconnect_telegram(me: CurrentUser) -> Response:
    service.disconnect_telegram(me.id)
    return see_other("/account")


@router.post("/unlink/{provider}")
def unlink(request: Request, me: CurrentUser, provider: str) -> Response:
    unlinked = service.unlink(me.id, provider)
    if unlinked.user is None:
        return _page(request, me, unlinked.notice)
    AuthMiddleware.sign_in(request, unlinked.user)
    return see_other("/account")
