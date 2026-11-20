import secrets

from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, ValidationError
lazy from fastapi.responses import Response

from ofmhelpers.config import settings
from ofmhelpers.integrations.signin import SignInError

STATE_COOKIE = "ofm_signin"
_COOKIE_PATH = "/auth/"
HOME = "/home"
_SPACE = 0x20


class SignInState(BaseModel):
    provider: str
    state: str
    nonce: str
    next: str = HOME
    link_user_id: int | None = None


def safe_next(next_url: str | None) -> str:
    if (
        not next_url
        or next_url == "/"
        or not next_url.startswith("/")
        or next_url.startswith("//")
        or "\\" in next_url
        or any(ord(c) < _SPACE for c in next_url)
    ):
        return HOME
    return next_url


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session.session_secret, salt="signin")


def seal(carried: SignInState) -> str:
    return _serializer().dumps(carried.model_dump())


def unseal(cookie: str, name: str, state: str) -> SignInState:
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
    secure = settings.session.session_https_only
    response.set_cookie(
        STATE_COOKIE,
        sealed,
        max_age=settings.signin.state_ttl_s,
        httponly=True,
        secure=secure,
        samesite="none" if secure else "lax",
        path=_COOKIE_PATH,
    )


def drop_state(response: Response) -> None:
    response.delete_cookie(STATE_COOKIE, path=_COOKIE_PATH)
