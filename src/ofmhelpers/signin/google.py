"""Sign in with Google, and connect Google Drive so uploads go up as the
person (a service account has no storage: `storageQuotaExceeded`).

OAuth "Web application" client of the Drive bot's Cloud project
(`settings.signin.google_*`). Signing in asks only `openid email profile`
(non-sensitive: no warning screen, no verification). Drive is asked for
only when someone connects it (/account, `connect=True`): the full `drive`
scope is restricted, so until Google verifies the app (a paid security
assessment) that one step shows "Google hasn't verified this app" once
(Advanced -> continue), and at most 100 accounts may use it.
https://developers.google.com/identity/protocols/oauth2/web-server
https://developers.google.com/identity/openid-connect/openid-connect
"""

from __future__ import annotations

from urllib.parse import urlencode

from google.oauth2.credentials import Credentials

from ofmhelpers.config import settings
from ofmhelpers.gdrive.client import SCOPES as DRIVE_SCOPES
from ofmhelpers.signin.base import (
    Identity,
    SignInError,
    TokenReply,
    checked,
    claims,
    token_request,
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 -- a URL
SIGN_IN_SCOPES = ("openid", "email", "profile")
CONNECT_SCOPES = (*SIGN_IN_SCOPES, *DRIVE_SCOPES)


def _client() -> tuple[str, str]:
    s = settings.signin
    if not s.google_configured or s.google_client_secret is None:
        msg = "Sign in with Google is not set up"
        raise SignInError(msg)
    return s.google_client_id, s.google_client_secret.get_secret_value()


class Google:
    name = "google"
    label = "Google"
    callback_method = "GET"
    local_consent = False
    linkable = True

    def configured(self) -> bool:
        return settings.signin.google_configured

    def consent_url(
        self, redirect_uri: str, state: str, nonce: str, *, connect: bool
    ) -> str:
        """Sign-in: who someone is, nothing else. `connect`: Drive too, on
        a consent screen with offline access -- the only way Google hands
        out a refresh token."""
        client_id, _secret = _client()
        query = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(CONNECT_SCOPES if connect else SIGN_IN_SCOPES),
            "state": state,
            "nonce": nonce,
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

    def exchange(self, code: str, redirect_uri: str) -> TokenReply:
        client_id, secret = _client()
        return token_request(
            TOKEN_URL,
            {
                "code": code,
                "client_id": client_id,
                "client_secret": secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            self.label,
        )

    def identity(self, reply: TokenReply, nonce: str) -> Identity:
        found = checked(claims(reply.id_token), nonce)
        return Identity(
            provider=self.name,
            subject=found.sub,
            email=found.email,
            email_verified=found.email_verified,
            name=found.name,
            refresh_token=reply.refresh_token,
        )


def drive_credentials(refresh_token: str) -> Credentials:
    """Credentials that mint access tokens from a person's stored refresh
    token: what `gdrive.connect_as` uploads with."""
    client_id, secret = _client()
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URL,
        client_id=client_id,
        client_secret=secret,
        # No `scopes`: a refresh keeps what was granted, and google-auth
        # would warn that "email"/"profile" came back under their long
        # userinfo.* names.
    )
