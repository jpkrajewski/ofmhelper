# Copyright (C) 2026 jpkrajewski

import logging
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator
lazy from sqlalchemy.engine import Dialect

from ofmhelpers.config import settings

logger = logging.getLogger(__name__)


class VaultError(RuntimeError): ...


@lru_cache(maxsize=4)
def _fernet(keys: tuple[str, ...]) -> MultiFernet:
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
