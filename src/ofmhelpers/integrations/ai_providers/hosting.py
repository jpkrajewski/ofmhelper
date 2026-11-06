"""This server as the file host of a provider that has no upload endpoint
(APIYI: "host local files on your own OSS/CDN and pass public URLs"): each
reference file gets a link of its own the provider fetches,
`<public base>/files/ai/<token>/ref<suffix>` (the suffix for providers that
read the type from it; never the file's own name).

The token is 32 random bytes; Redis maps it to the file's path
(`aigen:hosted:<token>`) for `OFM_AIGEN_HOSTED_TTL_S`, so a link names one
file, cannot be guessed or walked, and dies on its own (3 h: the provider
fetches when its task starts). The route serving it is
`web/routers/ai_files.py` (public: `/files/ai/` in `public_prefixes`). The
same file is handed the same link while it is remembered (`uploads.memoized`,
at most half the link's life, per public address: a new tunnel address never
gets an old address's link). The token is never logged.

The base is `OFM_AIGEN_PUBLIC_BASE_URL`, else APP_BASE_URL, and must be
HTTPS: nothing outside can fetch from a localhost dev, so a run with
references is refused there before anything is paid (dev: a tunnel's
address in `OFM_AIGEN_PUBLIC_BASE_URL`).
"""

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
# token_urlsafe(32) is 43 characters of [A-Za-z0-9_-].
_TOKEN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)
_TOKEN_MAX = 64


def public_base() -> str:
    """This server's public HTTPS address, without the trailing slash; empty
    when it has none."""
    base = settings.aigen.public_base_url or settings.web.base_url
    return base.rstrip("/") if base.startswith("https://") else ""


def link(path: Path, label: str, reuse_s: int) -> str:
    """A public link to `path` for `label` (the provider) to fetch; the same
    one while remembered (`reuse_s`, below the link's own life)."""
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
        # Without it the link would answer 404 and the provider fail the run.
        msg = f"Could not share {path.name}: Redis unavailable"
        raise ProviderUnavailableError(msg, maybe_made=False) from exc
    logger.info("hosted %s for %s s", path.name, ttl_s)
    return f"{base}{HOSTED_PATH}/{token}/ref{quote(path.suffix.lower())}"


def _token_of(url: str) -> str:
    rest = url.split(f"{HOSTED_PATH}/", 1)[-1]
    return rest.split("/", 1)[0]


def hosted_file(token: str) -> Path | None:
    """The file `token` links to, while the link lives and the file exists."""
    if not token or len(token) > _TOKEN_MAX or not set(token) <= _TOKEN_CHARS:
        return None
    try:
        found = get_redis().get(f"{_PREFIX}{token}")
    except RedisError:
        # Not `cache.get_text`: its warning would log the key, the token in it.
        logger.warning("hosted file lookup unavailable (Redis)")
        return None
    if found is None:
        return None
    path = Path(found.decode() if isinstance(found, bytes) else str(found))
    return path if path.is_file() else None
