"""Password hashing and Apple's client-secret JWT."""

import base64
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from ofmhelpers.signin import SignInError
from ofmhelpers.signin.apple import client_secret
from ofmhelpers.signin.base import checked, claims
from ofmhelpers.signin.passwords import (
    hash_password,
    link_hash,
    new_link_token,
    verify_password,
)


def test_a_password_verifies_and_another_does_not():
    stored = hash_password("correct horse battery")
    assert stored.startswith("scrypt$131072$8$1$")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("correct horse batterY", stored)


def test_the_same_password_hashes_differently_each_time():
    assert hash_password("same") != hash_password("same")


@pytest.mark.parametrize("stored", ["", "plain", "scrypt$x$y$z$a$b", "md5$1$2$3$4$5"])
def test_an_unusable_stored_hash_never_verifies(stored):
    assert not verify_password("anything", stored)


def test_a_link_token_is_stored_only_as_its_hash():
    token, stored = new_link_token()
    assert len(token) >= 40
    assert stored == link_hash(token) != token


def _b64d(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_apples_client_secret_is_an_es256_jwt_its_key_verifies(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("OFM_APPLE_SERVICES_ID", "cash.ascendglobal.web")
    monkeypatch.setenv("OFM_APPLE_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("OFM_APPLE_KEY_ID", "KEY1234567")
    monkeypatch.setenv("OFM_APPLE_PRIVATE_KEY", pem.replace("\n", "\\n"))

    token = client_secret(now=1_800_000_000)
    header, payload, signature = token.split(".")

    assert json.loads(_b64d(header)) == {"alg": "ES256", "kid": "KEY1234567"}
    assert json.loads(_b64d(payload)) == {
        "iss": "TEAM123456",
        "iat": 1_800_000_000,
        "exp": 1_800_000_300,
        "aud": "https://appleid.apple.com",
        "sub": "cash.ascendglobal.web",
    }
    raw = _b64d(signature)
    assert len(raw) == 64
    der = encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:]))
    key.public_key().verify(
        der, f"{header}.{payload}".encode(), ec.ECDSA(hashes.SHA256())
    )
    with pytest.raises(InvalidSignature):
        key.public_key().verify(der, b"tampered", ec.ECDSA(hashes.SHA256()))


def test_apple_without_its_key_is_not_set_up(monkeypatch):
    monkeypatch.delenv("OFM_APPLE_PRIVATE_KEY", raising=False)
    with pytest.raises(SignInError, match="not set up"):
        client_secret()


def _id_token(claims_: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims_).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


def test_id_token_claims_apples_string_true_and_the_nonce_check():
    found = claims(
        _id_token(
            {"sub": "s", "email": "A@B.C", "email_verified": "true", "nonce": "n"}
        )
    )
    assert (found.email, found.email_verified) == ("a@b.c", True)
    assert checked(found, "n") is found
    with pytest.raises(SignInError):
        checked(found, "other")
    with pytest.raises(SignInError):
        claims("not-a-jwt")


def test_at_most_the_configured_number_of_hashes_run_at_once(monkeypatch):
    """A /login burst waits for a slot instead of allocating 128 MiB each."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from ofmhelpers.signin import passwords

    monkeypatch.setenv("OFM_PASSWORD_HASH_CONCURRENCY", "2")
    passwords._slots.cache_clear()
    lock = threading.Lock()
    running = 0
    peak = 0

    def scrypt(*args, **kwargs):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return b"\0" * 32

    monkeypatch.setattr(passwords.hashlib, "scrypt", scrypt)
    try:
        with ThreadPoolExecutor(12) as pool:
            list(pool.map(lambda _: verify_password("x", ""), range(12)))
    finally:
        passwords._slots.cache_clear()

    assert peak == 2


def test_a_hash_made_with_other_parameters_still_verifies():
    """Stored hashes carry their own n/r/p: changing the defaults (or the
    concurrency cap) never locks anyone out."""
    import base64 as b64
    import hashlib

    salt = b"0123456789abcdef"
    key = hashlib.scrypt(b"older password", salt=salt, n=2**14, r=8, p=1, dklen=32)
    stored = f"scrypt${2**14}$8$1${b64.b64encode(salt).decode()}${b64.b64encode(key).decode()}"
    assert verify_password("older password", stored)
    assert not verify_password("other password", stored)
