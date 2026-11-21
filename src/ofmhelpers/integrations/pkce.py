import base64
import hashlib
import secrets

from pydantic import BaseModel, ConfigDict


class Pkce(BaseModel):
    model_config = ConfigDict(frozen=True)

    verifier: str
    challenge: str


def pkce_pair() -> Pkce:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return Pkce(
        verifier=verifier,
        challenge=base64.urlsafe_b64encode(digest).rstrip(b"=").decode(),
    )
