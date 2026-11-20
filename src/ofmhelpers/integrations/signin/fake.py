import base64
import json
from urllib.parse import parse_qs, urlsplit
lazy from collections.abc import Mapping

from pydantic import BaseModel, ValidationError
lazy import requests

from ofmhelpers.integrations.outbound import FakeReply, FakeRoute

_PREFIX = "fake:"
_HOSTS = ("oauth2.googleapis.com", "appleid.apple.com")


class FakeAccount(BaseModel):
    email: str
    nonce: str
    subject: str = ""
    name: str = ""
    email_verified: bool = True
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
    except ValueError, ValidationError:  # silent: not a fake code
        return None


def _id_token(host: str, account: FakeAccount) -> str:
    claims = {
        "iss": f"https://{host}",
        "sub": account.subject or f"{host}:{account.email}",
        "email": account.email,
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


def routes() -> list[FakeRoute]:
    def handle(
        request: requests.PreparedRequest, _proxies: Mapping[str, str]
    ) -> FakeReply:
        return token_reply(request)

    return [FakeRoute(host=host, handler=handle) for host in _HOSTS]
