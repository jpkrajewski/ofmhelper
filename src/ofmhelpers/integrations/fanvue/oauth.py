"""Fanvue has no API keys: someone authorises the app once in their browser and it
keeps tokens. OAuth 2.0 authorization code with PKCE; the client authenticates by
HTTP Basic only. Refresh tokens are single-use and rotate, and reusing one kills the
whole authorisation, so whoever refreshes must save the new one first
(`db/repositories/chats.py` `fresh_token` holds a lock across it)."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from http import HTTPStatus
from urllib.parse import urlencode

import requests
from pydantic import ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations.fanvue.errors import FanvueError
from ofmhelpers.integrations.fanvue.models import TokenReply
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import ChatTokens
from ofmhelpers.utils import outbound

logger = get_logger(__name__)


def pkce_pair() -> tuple[str, str]:
    """(verifier, challenge): the challenge is the verifier's SHA-256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def authorize_url(redirect_uri: str, state: str, challenge: str) -> str:
    query = urlencode(
        {
            "client_id": settings.fanvue.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": settings.fanvue.scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{settings.fanvue.authorize_url}?{query}"


def _token_call(fields: dict[str, str]) -> requests.Response:
    session = outbound.mount(requests.Session())
    return session.post(
        settings.fanvue.token_url,
        data=fields,
        auth=(settings.fanvue.client_id, settings.fanvue.client_secret),
        timeout=settings.fanvue.timeout_s,
    )


def _tokens(r: requests.Response) -> ChatTokens:
    if not r.ok:
        logger.warning("fanvue token call answered %s", r.status_code)
        msg = f"Fanvue token error {r.status_code}"
        raise FanvueError(msg)
    try:
        reply = TokenReply.model_validate(r.json())
    except (ValueError, ValidationError):
        msg = "Fanvue answered an unexpected token reply"
        raise FanvueError(msg) from None
    return ChatTokens(
        access_token=reply.access_token,
        refresh_token=reply.refresh_token,
        expires_at=time.time() + reply.expires_in,
    )


def exchange(code: str, redirect_uri: str, verifier: str) -> ChatTokens:
    fields = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    try:
        return _tokens(_token_call(fields))
    except requests.RequestException as e:
        msg = "Fanvue is not answering"
        raise FanvueError(msg) from e


def _refresh_call(refresh_token: str) -> requests.Response:
    """A token whose answer got lost may be spent already. Fanvue says to present it
    again straight away, inside its grace window, and that waiting breaks the chain:
    so several tries, with no pause."""
    tries = settings.fanvue.refresh_tries
    for attempt in range(1, tries + 1):
        try:
            r = _token_call(
                {"grant_type": "refresh_token", "refresh_token": refresh_token}
            )
        except requests.RequestException as e:
            if attempt == tries:
                msg = "Fanvue is not answering"
                raise FanvueError(msg) from e
            continue
        if r.status_code < HTTPStatus.INTERNAL_SERVER_ERROR or attempt == tries:
            return r
    msg = "Fanvue is not answering"
    raise FanvueError(msg)


def refresh(refresh_token: str) -> ChatTokens | None:
    """New tokens; None when the authorisation is gone (`invalid_grant`) and someone
    has to connect again. Raises `FanvueError` for what a later try may fix."""
    r = _refresh_call(refresh_token)
    if r.status_code == HTTPStatus.BAD_REQUEST and _error_field(r, "error") == (
        "invalid_grant"
    ):
        logger.warning(
            "fanvue refused a refresh token: invalid_grant: %s",
            _error_field(r, "error_description"),
        )
        return None
    return _tokens(r)


def _error_field(r: requests.Response, name: str) -> str:
    try:
        return str(r.json().get(name, ""))
    except (ValueError, AttributeError):
        return ""
