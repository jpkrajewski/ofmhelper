from urllib.parse import urlencode

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.signin.consent import redirect_uri
from ofmhelpers.features.signin.outcome import Outcome, signed_in
from ofmhelpers.features.signin.state import (
    HOME,
    SignInState,
    safe_next,
    take_verifier,
    unseal,
)
from ofmhelpers.integrations.signin import Identity, Provider, SignInError
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


class Callback(BaseModel):
    code: str
    state: str
    error: str
    cookie: str
    site: str
    signed_in: bool


class _Verified(BaseModel):
    carried: SignInState
    identity: Identity


def _identity(provider: Provider, back: Callback) -> _Verified:
    if back.error:
        msg = f"{provider.label} sign-in was cancelled"
        raise SignInError(msg)
    carried = unseal(back.cookie, provider.name, back.state)
    reply = provider.exchange(
        back.code, redirect_uri(provider, back.site), take_verifier(carried.state)
    )
    return _Verified(carried=carried, identity=provider.identity(reply, carried.nonce))


def _sync_new_drive(user_id: int, refresh_token: str | None) -> None:
    if refresh_token:
        enqueue(drive_folders.sync, user_id)


def _link(
    provider: Provider, carried: SignInState, user_id: int, identity: Identity
) -> Outcome:
    owner = users.get_user(user_id)
    may_link = owner is not None and owner.active and owner.role != Role.VA
    linked = may_link and users.link_identity(user_id, identity)
    note = "linked" if linked else "taken"
    if linked:
        _sync_new_drive(user_id, identity.refresh_token)
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
        query = urlencode({"link": "1", "next": safe_next(carried.next)})
        outcome.redirect = f"/auth/{provider.name}/start?{query}"
    return outcome


def finish(provider: Provider, back: Callback) -> Outcome:
    try:
        verified = _identity(provider, back)
    except SignInError as exc:
        logger.warning("sign-in with %s failed: %s", provider.name, exc, exc_info=True)
        if back.signed_in:
            return Outcome(redirect=f"/account?{provider.name}=failed")
        return Outcome(error=str(exc), status_code=400)
    carried, identity = verified.carried, verified.identity
    if carried.link_user_id is not None:
        return _link(provider, carried, carried.link_user_id, identity)
    return _sign_in(provider, carried, identity)
