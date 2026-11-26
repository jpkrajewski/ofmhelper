# Copyright (C) 2026 jpkrajewski

from urllib.parse import urlencode

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.users.schema import Identity
from ofmhelpers.integrations.signin.base import (
    SignInError,
    TokenReply,
    checked,
    claims,
    token_request,
)
from ofmhelpers.integrations.storage.gdrive.client import GOOGLE_TOKEN_URL
from ofmhelpers.integrations.storage.gdrive.client import SCOPES as DRIVE_SCOPES

__all__ = ["DRIVE_SCOPES"]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = GOOGLE_TOKEN_URL
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SIGN_IN_SCOPES = ("openid", "email", "profile")
CONNECT_SCOPES = (*SIGN_IN_SCOPES, *DRIVE_SCOPES)


class _OAuthClient(BaseModel):
    client_id: str
    client_secret: str


def _client() -> _OAuthClient:
    s = settings.signin
    if not s.google_configured or s.google_client_secret is None:
        msg = "Sign in with Google is not set up"
        raise SignInError(msg)
    return _OAuthClient(
        client_id=s.google_client_id,
        client_secret=s.google_client_secret.get_secret_value(),
    )


class Google:
    name = "google"
    label = "Google"
    callback_method = "GET"
    local_consent = False
    linkable = True
    connects_drive = True

    def configured(self) -> bool:
        return settings.signin.google_configured

    def consent_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        *,
        connect: bool,
        challenge: str,
    ) -> str:
        client_id = _client().client_id
        query = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(CONNECT_SCOPES if connect else SIGN_IN_SCOPES),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if connect:
            query |= {
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
            }
        else:
            query["prompt"] = "select_account"
        return f"{AUTH_URL}?{urlencode(query)}"

    def exchange(self, code: str, redirect_uri: str, verifier: str) -> TokenReply:
        oauth = _client()
        form = {
            "code": code,
            "client_id": oauth.client_id,
            "client_secret": oauth.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if verifier:
            form["code_verifier"] = verifier
        return token_request(TOKEN_URL, form, self.label)

    def identity(self, reply: TokenReply, nonce: str) -> Identity:
        found = checked(
            claims(reply.id_token),
            nonce,
            issuers=ISSUERS,
            audience=_client().client_id,
        )
        return Identity(
            provider=self.name,
            subject=found.sub,
            email=found.email,
            email_verified=found.email_verified,
            name=found.name,
            refresh_token=reply.refresh_token,
        )
