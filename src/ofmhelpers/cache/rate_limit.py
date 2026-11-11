"""Fixed-window counters per client: failed logins and refused webhooks, and
the write ceiling `web/middleware/ratelimit.py` enforces.

The counters live on the one Redis connection (`get_redis`): shared state, so
a limit holds across every uvicorn worker. Redis failures fail *open* (allowed,
warning logged): a broken broker must not lock a working login form.

The client is `request.client.host` (see `web/middleware/ratelimit.py`).
"""

from redis.exceptions import RedisError
lazy from starlette.requests import Request

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

LOGIN_BUCKET = "login_failures"
WRITE_BUCKET = "write"
WEBHOOK_BUCKET = "webhook_refused"


def client_id(request: Request) -> str:
    client = request.client
    return client.host if client is not None else "unknown"


def counter_key(bucket: str, identifier: str) -> str:
    return f"ratelimit:{bucket}:{identifier}"


def hit(bucket: str, identifier: str, window_s: int) -> int:
    """Count one event in the current window; returns the new count (0 when
    Redis is unreachable, i.e. treat as under the limit)."""
    key = counter_key(bucket, identifier)
    try:
        redis = get_redis()
        count = int(redis.incr(key))
        # Fixed window: the TTL starts with the first event and the whole
        # counter disappears at the end of it. `nx=True` means this only sets
        # a TTL when the key has none, so it is safe to call on every hit --
        # and it must be, not just when count == 1. If the INCR lands and the
        # EXPIRE then fails (broker blip, failover), a count-gated expire is
        # never retried: the key lives forever with no TTL and that IP is
        # locked out of /login permanently, which is the opposite of the
        # fail-open this module promises. Calling it every time repairs a
        # missing TTL on the next request instead.
        redis.expire(key, window_s, nx=True)
    except RedisError:
        logger.warning("rate limit counter unavailable, allowing", exc_info=True)
        return 0
    else:
        return count


def _count(bucket: str, identifier: str) -> int:
    try:
        raw = get_redis().get(counter_key(bucket, identifier))
    except RedisError:
        logger.warning("rate limit counter unavailable, allowing", exc_info=True)
        return 0
    return int(raw) if raw is not None else 0


def _clear(bucket: str, identifier: str) -> None:
    try:
        get_redis().delete(counter_key(bucket, identifier))
    except RedisError:
        logger.warning("rate limit counter unavailable, not cleared", exc_info=True)


def login_blocked(request: Request) -> bool:
    """True once this client has burned its failed-login allowance. Read-only
    -- the counter is only advanced by record_failed_login()."""
    s = settings.web
    if not s.rate_limit_enabled:
        return False
    return _count(LOGIN_BUCKET, client_id(request)) >= s.login_max_failures


def record_failed_login(request: Request) -> None:
    hit(LOGIN_BUCKET, client_id(request), settings.web.login_failure_window_s)


def clear_failed_logins(request: Request) -> None:
    _clear(LOGIN_BUCKET, client_id(request))


def webhook_blocked(request: Request) -> bool:
    """True once this client has sent too many deliveries failing the signature."""
    if not settings.web.rate_limit_enabled:
        return False
    count = _count(WEBHOOK_BUCKET, client_id(request))
    return count >= settings.chatting.webhook_max_refused


def record_refused_webhook(request: Request) -> None:
    hit(
        WEBHOOK_BUCKET,
        client_id(request),
        settings.chatting.webhook_refused_window_s,
    )
