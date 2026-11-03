"""Google's and Apple's token endpoints, in process (OFM_FAKE_EXTERNALS,
tests): a code from `fake_code` answers tokens for the account it names,
with the nonce it carries; any other code is the provider's invalid_grant.

Under OFM_FAKE_EXTERNALS the providers' consent screens are the app's own
`/auth/fake-consent` page (routers/auth.py), which mints such a code.
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ValidationError

from ofmhelpers.utils.outbound import FakeHandler, FakeReply

if TYPE_CHECKING:
    from collections.abc import Mapping

    import requests

_PREFIX = "fake:"
_HOSTS = ("oauth2.googleapis.com", "appleid.apple.com")


class FakeAccount(BaseModel):
    """Who a fake consent signs in as."""

    email: str
    nonce: str
    subject: str = ""
    name: str = ""
    email_verified: bool = True
    # Google sends a refresh token only on a consent screen: False is a
    # sign-in Google waved through (`prompt=none`), as for an account that
    # consented before.
    refresh_token: bool = True


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def fake_code(account: FakeAccount) -> str:
    return _PREFIX + _b64(account.model_dump_json().encode())


def _account(code: str) -> FakeAccount | None:
    if not code.startswith(_PREFIX):
        return None
    raw = code.removeprefix(_PREFIX)
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        return FakeAccount.model_validate_json(decoded)
    except (ValueError, ValidationError):
        return None


def _id_token(host: str, account: FakeAccount) -> str:
    claims = {
        "iss": f"https://{host}",
        "sub": account.subject or f"{host}:{account.email}",
        "email": account.email,
        # Apple sends it as a string.
        "email_verified": "true" if account.email_verified else "false",
        "name": account.name,
        "nonce": account.nonce,
    }
    header = _b64(json.dumps({"alg": "none"}).encode())
    return f"{header}.{_b64(json.dumps(claims).encode())}.sig"


def token_reply(request: requests.PreparedRequest) -> FakeReply:
    body = request.body or ""
    raw = body.decode() if isinstance(body, bytes) else str(body)
    account = _account(parse_qs(raw).get("code", [""])[0])
    if account is None:
        return FakeReply.json_body({"error": "invalid_grant"}, status=400)
    host = urlsplit(request.url or "").hostname or ""
    reply = {"access_token": "fake-access", "id_token": _id_token(host, account)}
    if host == "oauth2.googleapis.com" and account.refresh_token:
        reply["refresh_token"] = f"fake-refresh:{account.email}"
    return FakeReply.json_body(reply)


def routes() -> dict[str, FakeHandler]:
    def handle(
        request: requests.PreparedRequest, _proxies: Mapping[str, str]
    ) -> FakeReply:
        return token_reply(request)

    return dict.fromkeys(_HOSTS, handle)
