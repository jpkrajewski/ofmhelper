# Copyright (C) 2026 jpkrajewski

import base64
import json
import time
from typing import Protocol, runtime_checkable

import requests
from pydantic import BaseModel, ValidationError, field_validator

from ofmhelpers.integrations import outbound
lazy from ofmhelpers.db.users.schema import Identity

_TIMEOUT_S = 20


class SignInError(Exception): ...


class Claims(BaseModel):
    sub: str
    email: str = ""
    email_verified: bool = False
    name: str = ""
    nonce: str = ""
    iss: str = ""
    aud: str | list[str] = ""
    exp: float = 0

    @field_validator("email", mode="after")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()


class TokenReply(BaseModel):
    id_token: str
    refresh_token: str | None = None


@runtime_checkable
class Provider(Protocol):
    @property
    def name(self) -> str: ...

    label: str
    callback_method: str
    local_consent: bool
    linkable: bool
    connects_drive: bool

    def configured(self) -> bool: ...

    def consent_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        *,
        connect: bool,
        challenge: str,
    ) -> str: ...

    def exchange(self, code: str, redirect_uri: str, verifier: str) -> TokenReply: ...

    def identity(self, reply: TokenReply, nonce: str) -> Identity: ...


def claims(id_token: str) -> Claims:
    try:
        payload = id_token.split(".")[1]
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        return Claims.model_validate(json.loads(raw))
    except (IndexError, ValueError, ValidationError) as exc:
        msg = "The sign-in answer could not be read"
        raise SignInError(msg) from exc


def checked(
    found: Claims,
    nonce: str,
    *,
    issuers: tuple[str, ...],
    audience: str,
    now: float | None = None,
) -> Claims:
    audiences = [found.aud] if isinstance(found.aud, str) else found.aud
    current = time.time() if now is None else now
    if (
        not nonce
        or found.nonce != nonce
        or found.iss not in issuers
        or audience not in audiences
        or found.exp <= current
    ):
        msg = "The sign-in answer did not match this sign-in: try again"
        raise SignInError(msg)
    return found


def token_request(url: str, data: dict[str, str], what: str) -> TokenReply:
    try:
        response = outbound.post(url, data=data, timeout=_TIMEOUT_S)
    except requests.RequestException as exc:
        msg = f"{what} sign-in failed: {exc}"
        raise SignInError(msg) from exc
    if not response.ok:
        msg = f"{what} refused the sign-in ({response.status_code})"
        raise SignInError(msg)
    try:
        return TokenReply.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        msg = f"{what} answered the sign-in with something unreadable"
        raise SignInError(msg) from exc
