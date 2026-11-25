# Copyright (C) 2026 jpkrajewski

import logging
import secrets

from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError
lazy from fastapi.responses import Response

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.integrations.signin import SignInError

logger = logging.getLogger(__name__)

STATE_COOKIE = "ofm_signin"
_COOKIE_PATH = "/auth/"
HOME = "/home"
_SPACE = 0x20
_VERIFIER_KEY = "signin:verifier:{state}"


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
            _serializer().loads(cookie, max_age=settings.signin.state_ttl_s),
        )
    except (BadSignature, ValidationError) as exc:
        msg = "This sign-in took too long or was not started here"
        raise SignInError(msg) from exc
    if carried.provider != name or not secrets.compare_digest(carried.state, state):
        msg = "This sign-in was not started here: try again"
        raise SignInError(msg)
    return carried


def keep_verifier(state: str, verifier: str) -> None:
    get_redis().set(
        _VERIFIER_KEY.format(state=state),
        verifier,
        ex=settings.signin.state_ttl_s,
    )


def take_verifier(state: str) -> str:
    try:
        raw = get_redis().getdel(_VERIFIER_KEY.format(state=state))
    except RedisError as exc:
        logger.warning("sign-in: verifier store unreachable", exc_info=True)
        msg = "Sign-in did not work. Try again."
        raise SignInError(msg) from exc
    if raw is None:
        msg = "This sign-in took too long or was already used: try again"
        raise SignInError(msg)
    return raw.decode() if isinstance(raw, bytes) else raw


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
