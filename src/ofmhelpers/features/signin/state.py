"""What a consent round trip carries, in a short-lived signed cookie rather
than the session: Apple's cross-site POST does not carry a SameSite=Lax
session cookie."""

import secrets

from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, ValidationError
lazy from fastapi.responses import Response

from ofmhelpers.config import settings
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.web.middleware import AuthMiddleware

STATE_COOKIE = "ofm_signin"
_COOKIE_PATH = "/auth/"
HOME = AuthMiddleware.HOME


class SignInState(BaseModel):
    """What a consent round trip carries in `STATE_COOKIE`."""

    provider: str
    state: str
    nonce: str
    next: str = HOME
    # Set: link the account to this signed-in user instead of signing in.
    link_user_id: int | None = None


def safe_next(next_url: str | None) -> str:
    """Only a path on this site: never `//evil` or `https://evil`; nothing
    (or the landing page) is the app's home."""
    if (
        not next_url
        or next_url == "/"
        or not next_url.startswith("/")
        or next_url.startswith("//")
    ):
        return HOME
    return next_url


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session.session_secret, salt="signin")


def seal(carried: SignInState) -> str:
    """The cookie's value."""
    return _serializer().dumps(carried.model_dump())


def unseal(cookie: str, name: str, state: str) -> SignInState:
    """The cookie's content, when it is this sign-in's. Raises `SignInError`."""
    try:
        carried = SignInState.model_validate(
            _serializer().loads(cookie, max_age=settings.signin.state_ttl_s)
        )
    except (BadSignature, ValidationError) as exc:
        msg = "This sign-in took too long or was not started here"
        raise SignInError(msg) from exc
    if carried.provider != name or not secrets.compare_digest(carried.state, state):
        msg = "This sign-in was not started here: try again"
        raise SignInError(msg)
    return carried


def carry_state(response: Response, sealed: str) -> None:
    """Puts the cookie on the response leaving for the consent screen."""
    secure = settings.session.session_https_only
    response.set_cookie(
        STATE_COOKIE,
        sealed,
        max_age=settings.signin.state_ttl_s,
        httponly=True,
        secure=secure,
        # Apple comes back with a cross-site POST: only SameSite=None rides
        # along, and browsers take that only on a Secure cookie.
        samesite="none" if secure else "lax",
        path=_COOKIE_PATH,
    )


def drop_state(response: Response) -> None:
    response.delete_cookie(STATE_COOKIE, path=_COOKIE_PATH)
