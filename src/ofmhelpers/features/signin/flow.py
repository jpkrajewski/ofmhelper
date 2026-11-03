"""Finishing a sign-in: from the provider's return to an `Outcome`."""

from urllib.parse import urlencode

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.db.stores import users
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.signin.consent import redirect_uri
from ofmhelpers.features.signin.outcome import Outcome, signed_in
from ofmhelpers.features.signin.state import HOME, SignInState, safe_next, unseal
from ofmhelpers.integrations.signin import Identity, Provider, SignInError
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class Callback(BaseModel):
    """What came back: the provider's params and this browser's request."""

    code: str
    state: str
    error: str
    cookie: str
    # This request's base URL, for the redirect URI the exchange repeats.
    site: str
    # Someone already has a session in this browser.
    signed_in: bool


def _identity(provider: Provider, back: Callback) -> tuple[SignInState, Identity]:
    """Who the callback says signed in. Raises `SignInError`."""
    if back.error:
        msg = f"{provider.label} sign-in was cancelled"
        raise SignInError(msg)
    carried = unseal(back.cookie, provider.name, back.state)
    reply = provider.exchange(back.code, redirect_uri(provider, back.site))
    return carried, provider.identity(reply, carried.nonce)


def _sync_new_drive(user_id: int, refresh_token: str | None) -> None:
    """A Drive token just arrived: build that person's root and account
    folders now (on the worker) instead of at the next sweep, so /clone's
    upload and /drive work straight after connecting."""
    if refresh_token:
        enqueue(drive_folders.sync, user_id)


def _link(
    provider: Provider, carried: SignInState, user_id: int, identity: Identity
) -> Outcome:
    linked = users.link_identity(user_id, identity)
    note = "linked" if linked else "taken"
    if linked:
        _sync_new_drive(user_id, identity.refresh_token)
    # Connected from a page (/clone's "Connect Google Drive", the sign-in's
    # own Drive step): back to that page; else /account says how it went.
    back = carried.next if linked and carried.next not in (HOME, "/account") else None
    return Outcome(redirect=back or f"/account?{provider.name}={note}")


def _sign_in(provider: Provider, carried: SignInState, identity: Identity) -> Outcome:
    user = users.sign_in_identity(identity)
    if user is None:
        logger.warning("sign-in with %s refused for %s", provider.name, identity.email)
        return Outcome(
            next_url=carried.next,
            error=f"{identity.email or 'This account'} has no access. Ask an admin"
            " to add it on Users.",
            status_code=403,
        )
    _sync_new_drive(user.id, identity.refresh_token)
    outcome = signed_in(user, carried.next, provider.name)
    drive = users.get_user(user.id)
    if provider.connects_drive and drive and not drive.drive_connected:
        # Signed in, but this account has no Drive yet: the Drive consent
        # follows at once instead of a second "Connect" someone has to find
        # (prod, 2026-10-22: the VA signed in with the admin Google account,
        # then had to connect again).
        query = urlencode({"link": "1", "next": safe_next(carried.next)})
        outcome.redirect = f"/auth/{provider.name}/start?{query}"
    return outcome


def finish(provider: Provider, back: Callback) -> Outcome:
    try:
        carried, identity = _identity(provider, back)
    except SignInError as exc:
        logger.warning("sign-in with %s failed: %s", provider.name, exc, exc_info=True)
        # Someone already signed in was connecting an account (Drive): back to
        # /account to try again, not a login page that invites a plain
        # sign-in, which never asks for Drive (prod, 2026-10-22).
        if back.signed_in:
            return Outcome(redirect=f"/account?{provider.name}=failed")
        return Outcome(error=str(exc), status_code=400)
    if carried.link_user_id is not None:
        return _link(provider, carried, carried.link_user_id, identity)
    return _sign_in(provider, carried, identity)
