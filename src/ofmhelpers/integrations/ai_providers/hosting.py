# Copyright (C) 2026 jpkrajewski

import secrets
from pathlib import Path
from urllib.parse import quote

from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderInputError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.ai_providers.uploads import memoized
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

HOSTED_PATH = "/files/ai"
_PREFIX = "aigen:hosted:"
_TOKEN_BYTES = 32
_TOKEN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
)
_TOKEN_MAX = 64


def public_base() -> str:
    base = settings.aigen.public_base_url or settings.web.base_url
    return base.rstrip("/") if base.startswith("https://") else ""


def link(path: Path, label: str, reuse_s: int) -> str:
    base = public_base()
    if not base:
        msg = (
            f"{label} fetches {path.name} from this server, which has no public "
            "https address (APP_BASE_URL)"
        )
        raise ProviderInputError(msg)
    if not path.is_file():
        msg = f"{path.name} is not on this server any more"
        raise ProviderInputError(msg)
    ttl_s = settings.aigen.hosted_ttl_s
    return memoized(
        f"aigen:hosting:{base}:{path.resolve()}",
        min(reuse_s, ttl_s // 2),
        lambda: _new_link(base, path, ttl_s),
        live=lambda url: (
            url.startswith(f"{base}{HOSTED_PATH}/")
            and hosted_file(_token_of(url)) is not None
        ),
    )


def _new_link(base: str, path: Path, ttl_s: int) -> str:
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    try:
        get_redis().set(f"{_PREFIX}{token}", str(path.resolve()), ex=ttl_s)
    except RedisError as exc:
        msg = f"Could not share {path.name}: Redis unavailable"
        raise ProviderUnavailableError(msg, maybe_made=False) from exc
    logger.info("hosted %s for %s s", path.name, ttl_s)
    return f"{base}{HOSTED_PATH}/{token}/ref{quote(path.suffix.lower())}"


def _token_of(url: str) -> str:
    rest = url.split(f"{HOSTED_PATH}/", 1)[-1]
    return rest.split("/", 1)[0]


def hosted_file(token: str) -> Path | None:
    if not token or len(token) > _TOKEN_MAX or not set(token) <= _TOKEN_CHARS:
        return None
    try:
        found = get_redis().get(f"{_PREFIX}{token}")
    except RedisError:
        logger.warning("hosted file lookup unavailable (Redis)", exc_info=True)
        return None
    if found is None:
        return None
    path = Path(found.decode() if isinstance(found, bytes) else str(found))
    return path if path.is_file() else None
