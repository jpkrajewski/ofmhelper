"""Email + password: scrypt hashes (stdlib `hashlib.scrypt`, a memory-hard
KDF, OWASP's parameters) and one-time set-password links.

A stored hash is "scrypt$<n>$<r>$<p>$<salt>$<key>" (base64), so stronger
parameters later still verify old hashes.

Each derivation holds ~128 MiB, so at most `OFM_PASSWORD_HASH_CONCURRENCY`
run at once per process (the rest wait): a burst of /login POSTs, which all
pass the failed-login limit before any failure is counted, cannot take the
box's memory.
https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
from functools import lru_cache

from ofmhelpers.config import settings

_N, _R, _P = 2**17, 8, 1
_SALT_BYTES = 16
_KEY_BYTES = 32
_PARTS = 6
# Enough for n=2**17, r=8 (128 * n * r = 128 MiB) with headroom.
_MAXMEM = 256 * 1024 * 1024


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@lru_cache(maxsize=1)
def _slots() -> threading.BoundedSemaphore:
    """The process's derivation slots (sized once, at the first use)."""
    return threading.BoundedSemaphore(settings.signin.password_hash_concurrency)


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    with _slots():
        return hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=n,
            r=r,
            p=p,
            maxmem=_MAXMEM,
            dklen=_KEY_BYTES,
        )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    key = _derive(password, salt, _N, _R, _P)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time. False for an empty or unreadable `stored` -- after
    the same work, so a missing account is not told apart by timing."""
    parts = stored.split("$")
    if len(parts) != _PARTS or parts[0] != "scrypt":
        _derive(password, b"\0" * _SALT_BYTES, _N, _R, _P)
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, key = base64.b64decode(parts[4]), base64.b64decode(parts[5])
    except ValueError:
        return False
    return hmac.compare_digest(_derive(password, salt, n, r, p), key)


def new_link_token() -> tuple[str, str]:
    """(token for the URL, its SHA-256 to store)."""
    token = secrets.token_urlsafe(32)
    return token, link_hash(token)


def link_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
