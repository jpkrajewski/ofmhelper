"""
`EncryptedText`: a Text column stored Fernet-encrypted, read back as the
plaintext. The /phones logins (passwords, backup codes) use it, so a database
dump -- a backup, `tests/fixtures/prod_snapshot.sql` -- holds no usable
login without `OFM_VAULT_KEY` (settings.vault).

Fernet is authenticated: a value encrypted with another key fails loudly
(`VaultError`) instead of decrypting to garbage, and nothing ever reads back
as a silent empty string. An empty value is stored as "" (not encrypted):
it reveals only that there is no password, and lets a list say which logins
have one without decrypting any.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect

logger = get_logger(__name__)


class VaultError(RuntimeError):
    """The vault key is missing, malformed, or not the one a value was
    encrypted with."""


@lru_cache(maxsize=4)
def _fernet(keys: tuple[str, ...]) -> MultiFernet:
    """Per key set, so a test (or a rotation) that changes the env gets its
    own; the first key encrypts."""
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except ValueError as exc:
        msg = "OFM_VAULT_KEY is not a valid Fernet key (32 url-safe base64 bytes)"
        raise VaultError(msg) from exc


def vault() -> MultiFernet:
    keys = tuple(settings.vault.key_list())
    if not keys:
        msg = (
            "OFM_VAULT_KEY is not set: /phones logins cannot be read or saved. "
            'Generate one with `python -c "from cryptography.fernet import '
            'Fernet; print(Fernet.generate_key().decode())"` and add it to .env.'
        )
        raise VaultError(msg)
    return _fernet(keys)


def encrypt(plaintext: str) -> str:
    return vault().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return vault().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        logger.exception("A stored login does not decrypt with OFM_VAULT_KEY")
        msg = (
            "Stored logins do not decrypt with the current OFM_VAULT_KEY: it was "
            "changed or lost. Put the old key back (first in the list)."
        )
        raise VaultError(msg) from exc


class EncryptedText(TypeDecorator[str]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:  # noqa: ARG002
        return value if not value else encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:  # noqa: ARG002
        return value if not value else decrypt(value)
