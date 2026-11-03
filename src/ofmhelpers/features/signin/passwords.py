"""The password tries: /login and the app owner's consent page. Failures count
against the per-client brake in `web/middleware/ratelimit.py`, which keys on
the request: the only reason these take one."""

from urllib.parse import urlencode

from fastapi import Request

from ofmhelpers.db.stores import users
from ofmhelpers.features.signin.outcome import Outcome, signed_in
from ofmhelpers.features.signin.state import STATE_COOKIE, unseal
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.signin.owner import owner_code, password_ok
from ofmhelpers.log import get_logger
from ofmhelpers.web.middleware.ratelimit import (
    clear_failed_logins,
    client_id,
    login_blocked,
    record_failed_login,
)

logger = get_logger(__name__)

_TOO_MANY = "Too many failed attempts. Try again later."


def login(request: Request, email: str, password: str, next_url: str) -> Outcome:
    if login_blocked(request):
        logger.warning("login rate limit hit from %s", client_id(request))
        return Outcome(next_url=next_url, error=_TOO_MANY, status_code=429)
    user = users.password_sign_in(email, password)
    if user is None:
        # Every failure is logged and counted: the only signal that someone is
        # guessing, and the only brake on how fast they can.
        record_failed_login(request)
        # The email tried (never the password): which account to look at.
        logger.warning(
            "failed login attempt for %r from %s",
            email.strip()[:320],
            client_id(request),
        )
        return Outcome(
            next_url=next_url, error="Wrong email or password", status_code=401
        )
    clear_failed_logins(request)
    return signed_in(user, next_url, "password")


def owner_consent(request: Request, state: str, password: str) -> Outcome:
    """The right admin password goes on to the callback with a code for this
    one sign-in; a wrong one is counted like a failed /login."""
    if login_blocked(request):
        logger.warning("login rate limit hit from %s", client_id(request))
        return Outcome(error=_TOO_MANY, status_code=429)
    try:
        carried = unseal(request.cookies.get(STATE_COOKIE, ""), "owner", state)
    except SignInError as exc:
        return Outcome(error=str(exc), status_code=400)
    if not password_ok(password):
        record_failed_login(request)
        logger.warning("failed app owner sign-in from %s", client_id(request))
        return Outcome(error="Wrong admin password", status_code=401)
    clear_failed_logins(request)
    query = urlencode({"code": owner_code(carried.nonce), "state": state})
    return Outcome(redirect=f"/auth/owner/callback?{query}")
