"""Sign in with Apple.

Needs an Apple Developer account: a Services ID (`OFM_APPLE_SERVICES_ID`)
with Sign in with Apple on, its return URL `<APP_BASE_URL>auth/apple/callback`,
and a Sign in with Apple key (`OFM_APPLE_KEY_ID`, its .p8 as
`OFM_APPLE_PRIVATE_KEY`, team `OFM_APPLE_TEAM_ID`). The client secret is a
short-lived ES256 JWT signed with that key, made per exchange.

Apple answers by POSTing to the callback (`response_mode=form_post`, needed
for the email scope), and may hand out a private-relay email
(...@privaterelay.appleid.com): once linked, the account is found by its
Apple id whatever the email.
https://developer.apple.com/documentation/sign_in_with_apple/generate_and_validate_tokens
https://developer.apple.com/documentation/sign_in_with_apple/request_an_authorization_to_the_sign_in_with_apple_server
"""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import urlencode

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from ofmhelpers.config import settings
from ofmhelpers.signin.base import (
    Identity,
    SignInError,
    TokenReply,
    checked,
    claims,
    token_request,
)

ISSUER = "https://appleid.apple.com"
AUTH_URL = f"{ISSUER}/auth/authorize"
TOKEN_URL = f"{ISSUER}/auth/token"
# Apple accepts a client secret valid up to 6 months; one exchange needs
# minutes.
_SECRET_TTL_S = 300
_ES256_PART = 32


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def client_secret(now: float | None = None) -> str:
    """The ES256 JWT Apple takes as the client secret."""
    s = settings.signin
    if not s.apple_configured or s.apple_private_key is None:
        msg = "Sign in with Apple is not set up"
        raise SignInError(msg)
    pem = s.apple_private_key.get_secret_value().replace("\\n", "\n").encode()
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except ValueError as exc:
        msg = "OFM_APPLE_PRIVATE_KEY is not a .p8 private key"
        raise SignInError(msg) from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        msg = "OFM_APPLE_PRIVATE_KEY is not an EC (ES256) key"
        raise SignInError(msg)
    issued = int(now if now is not None else time.time())
    header = {"alg": "ES256", "kid": s.apple_key_id}
    payload = {
        "iss": s.apple_team_id,
        "iat": issued,
        "exp": issued + _SECRET_TTL_S,
        "aud": ISSUER,
        "sub": s.apple_services_id,
    }
    signing_input = (
        f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    )
    der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s_value = decode_dss_signature(der)
    raw = r.to_bytes(_ES256_PART, "big") + s_value.to_bytes(_ES256_PART, "big")
    return f"{signing_input}.{_b64(raw)}"


class Apple:
    name = "apple"
    label = "Apple"
    callback_method = "POST"

    def configured(self) -> bool:
        return settings.signin.apple_configured

    def consent_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        *,
        connect: bool,  # noqa: ARG002 -- Apple grants nothing more to ask for
    ) -> str:
        query = {
            "client_id": settings.signin.apple_services_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "form_post",
            "scope": "name email",
            "state": state,
            "nonce": nonce,
        }
        return f"{AUTH_URL}?{urlencode(query)}"

    def exchange(self, code: str, redirect_uri: str) -> TokenReply:
        return token_request(
            TOKEN_URL,
            {
                "code": code,
                "client_id": settings.signin.apple_services_id,
                "client_secret": client_secret(),
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
        )
