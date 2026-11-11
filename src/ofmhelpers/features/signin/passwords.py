"""The password tries: /login and the app owner's consent page. Failures count
against the per-client brake in `cache/rate_limit.py`, which keys on
the request: the only reason these take one."""

from urllib.parse import urlencode

lazy from fastapi import Request

from ofmhelpers.cache.rate_limit import (
    clear_failed_logins,
    client_id,
    login_blocked,
    record_failed_login,
)
from ofmhelpers.db.stores import users
from ofmhelpers.features.signin import credentials
from ofmhelpers.features.signin.outcome import Outcome, signed_in
from ofmhelpers.features.signin.state import STATE_COOKIE, unseal
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.signin.owner import owner_code, password_ok
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_TOO_MANY = "Too many failed attempts. Try again later."
# The account the login form signs into when no email is typed.
PASSWORD_ADMIN = users.PASSWORD_ADMIN


def login(request: Request, email: str, password: str, next_url: str) -> Outcome:
    if login_blocked(request):
        logger.warning("login rate limit hit from %s", client_id(request))
        return Outcome(next_url=next_url, error=_TOO_MANY, status_code=429)
    user = credentials.password_sign_in(email, password)
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


def va_login(request: Request, employer: str, password: str, next_url: str) -> Outcome:
    """ "Login as Virtual Assistant": the employer's email and the password
    the employer's VA was given on /users. Counted and braked like /login."""
    if login_blocked(request):
        logger.warning("login rate limit hit from %s", client_id(request))
        return Outcome(next_url=next_url, error=_TOO_MANY, status_code=429)
    user = credentials.va_sign_in(employer, password)
    if user is None:
        record_failed_login(request)
        logger.warning(
            "failed VA login for employer %r from %s",
            employer.strip()[:320],
            client_id(request),
        )
        return Outcome(
            next_url=next_url,
            error="Wrong employer email or password",
            status_code=401,
        )
    clear_failed_logins(request)
    return signed_in(user, next_url, "va")


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
