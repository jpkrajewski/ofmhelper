"""What every sign-in provider (Google, Apple, ...) offers, and how it fails.

A provider is an OAuth 2.0 / OpenID Connect authorization-code flow:
`consent_url` sends the browser to it, its callback brings a `code` back,
`exchange` trades the code for tokens at the provider's token endpoint --
server to server, over TLS, so the id token in that answer is taken as is
(OpenID Connect Core 3.1.3.7: TLS validation stands in for the signature
check when the token comes straight from the token endpoint). The `nonce`
in it must be the one this sign-in started with.
"""

from __future__ import annotations

import base64
import json
from typing import Protocol

import requests
from pydantic import BaseModel, ValidationError, field_validator

from ofmhelpers.utils import outbound

_TIMEOUT_S = 20


class SignInError(Exception):
    """The provider refused, failed, or answered something unusable; the
    message is safe to show."""


class Identity(BaseModel):
    """Who the provider says signed in."""

    provider: str
    # The provider's stable id for the account: what links it to a user.
    subject: str
    email: str = ""
    email_verified: bool = False
    name: str = ""
    # Google only: lets Drive uploads go up as this person.
    refresh_token: str | None = None


class Claims(BaseModel):
    """The id token claims every provider sends (Apple sends
    `email_verified` as the string "true")."""

    sub: str
    email: str = ""
    email_verified: bool = False
    name: str = ""
    nonce: str = ""

    @field_validator("email", mode="after")
    @classmethod
    def _lower(cls, value: str) -> str:
        return value.strip().lower()


class TokenReply(BaseModel):
    id_token: str
    refresh_token: str | None = None


class Provider(Protocol):
    """One way to sign in. `name` is its URL segment (/auth/<name>/...)."""

    name: str
    label: str
    # How the provider returns to /auth/<name>/callback: "GET" (query) or
    # "POST" (Apple's form_post).
    callback_method: str
    # Its consent screen is this app's own page (the app owner): used under
    # OFM_FAKE_EXTERNALS too, instead of /auth/fake-consent.
    local_consent: bool
    # May be linked to a signed-in person's account (/account).
    linkable: bool

    def configured(self) -> bool: ...

    def consent_url(
        self, redirect_uri: str, state: str, nonce: str, *, connect: bool
    ) -> str:
        """`connect`: linking to a signed-in user (/account), where Google
        also asks for Drive; a plain sign-in asks only who someone is."""
        ...

    def exchange(self, code: str, redirect_uri: str) -> TokenReply: ...

    def identity(self, reply: TokenReply, nonce: str) -> Identity: ...


def claims(id_token: str) -> Claims:
    """The id token's payload (see the module docstring for why its
    signature is not checked here)."""
    try:
        payload = id_token.split(".")[1]
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        return Claims.model_validate(json.loads(raw))
    except (IndexError, ValueError, ValidationError) as exc:
        msg = "The sign-in answer could not be read"
        raise SignInError(msg) from exc


def checked(found: Claims, nonce: str) -> Claims:
    if not nonce or found.nonce != nonce:
        msg = "The sign-in answer did not match this sign-in: try again"
        raise SignInError(msg)
    return found


def token_request(url: str, data: dict[str, str], what: str) -> TokenReply:
    """POSTs the code to a token endpoint. Raises `SignInError`."""
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
