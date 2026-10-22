"""
/account: the signed-in person's own settings -- how they sign in (Google,
Apple, a password), whether Drive uploads can go up as them (a Google
account linked with Drive), and their own API keys per generation
provider, which their forms pre-fill so their runs bill their account.
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response

from ofmhelpers.aigenproviders.base import provider_classes
from ofmhelpers.config import settings
from ofmhelpers.signin import configured, providers
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.schemas import ApiKeysForm
from ofmhelpers.web.stores import users
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/account", tags=["account"])

_PRIVATE = {"Cache-Control": "no-store"}
# How much of a saved key the page shows.
_KEY_TAIL = 4


def _keyed_providers() -> dict[str, str]:
    """name -> label of the providers a run needs a key for."""
    return {
        name: cls.label for name, cls in provider_classes().items() if cls.needs_key
    }


def _page(
    request: Request,
    *,
    message: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    me = AuthMiddleware.user(request)
    user = users.get_user(me.id)
    saved = users.api_keys(me.id)
    response = render(
        request,
        "account.html",
        user=user,
        linked={a.provider: a for a in users.linked_accounts(me.id)},
        providers=configured(),
        keys={
            name: (label, saved.get(name, "")[-_KEY_TAIL:])
            for name, label in _keyed_providers().items()
        },
        min_length=settings.signin.password_min_length,
        message=message,
        error=error,
        status_code=status_code,
    )
    response.headers.update(_PRIVATE)
    return response


@router.get("")
def account_page(request: Request, google: str = "", apple: str = ""):
    notes = {
        "linked": "{} account linked.",
        "taken": "That {} account is already linked to someone else.",
    }
    for label, outcome in (("Google", google), ("Apple", apple)):
        if outcome == "taken":
            return _page(request, error=notes["taken"].format(label), status_code=409)
        if outcome == "linked":
            return _page(request, message=notes["linked"].format(label))
    return _page(request)


@router.post("/password")
def change_password(
    request: Request,
    password: Annotated[str, Form()],
    confirm: Annotated[str, Form()],
    current: Annotated[str, Form()] = "",
):
    me = AuthMiddleware.user(request)
    try:
        users.change_password(me.id, current, password, confirm)
    except users.PasswordRejectedError as exc:
        return _page(request, error=str(exc), status_code=400)
    # Every other session of theirs is out now; this one stays in.
    user = users.get_user(me.id)
    if user is not None:
        AuthMiddleware.remember(request, user)
    return _page(request, message="Password saved.")


@router.post("/keys")
async def save_keys(request: Request) -> Response:
    """One form, one Save for every provider's key (fields `key_<provider>`;
    a Remove button posts `remove=<provider>`)."""
    data = await request.form()
    known = _keyed_providers()
    form = ApiKeysForm(
        keys={name: str(data.get(f"key_{name}", "")) for name in known},
        remove=str(data.get("remove") or "") or None,
    )
    if form.remove is not None and form.remove not in known:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return await run_in_threadpool(_save_keys, request, form, known)


def _save_keys(request: Request, form: ApiKeysForm, known: dict[str, str]) -> Response:
    user_id = AuthMiddleware.user(request).id
    if form.remove is not None:
        users.set_api_key(user_id, form.remove, "")
        return _page(request, message=f"{known[form.remove]} key removed.")
    if not form.keys:
        return _page(
            request, error="Nothing to save: paste a key first.", status_code=400
        )
    for name, key in form.keys.items():
        users.set_api_key(user_id, name, key)
    saved = " and ".join(known[name] for name in form.keys)
    return _page(
        request, message=f"{saved} {'key' if len(form.keys) == 1 else 'keys'} saved."
    )


@router.post("/unlink/{provider}")
def unlink(request: Request, provider: str):
    """Stops signing in with that account (and, for Google, Drive uploads
    as it). Refused when it is the only way left to sign in."""
    if provider not in providers():
        raise HTTPException(status_code=404, detail="Unknown provider")
    me = AuthMiddleware.user(request)
    user = users.get_user(me.id)
    others = [a for a in users.linked_accounts(me.id) if a.provider != provider]
    if user is not None and not user.has_password and not others:
        return _page(
            request,
            error="Set a password or link another account first: this is your only way in.",
            status_code=400,
        )
    users.unlink_identity(me.id, provider)
    return RedirectResponse(url="/account", status_code=303)
