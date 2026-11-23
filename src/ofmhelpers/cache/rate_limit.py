# Copyright (C) 2026 jpkrajewski

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
    key = counter_key(bucket, identifier)
    try:
        redis = get_redis()
        count = int(redis.incr(key))
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
    s = settings.web
    if not s.rate_limit_enabled:
        return False
    return _count(LOGIN_BUCKET, client_id(request)) >= s.login_max_failures


def record_failed_login(request: Request) -> None:
    hit(LOGIN_BUCKET, client_id(request), settings.web.login_failure_window_s)


def clear_failed_logins(request: Request) -> None:
    _clear(LOGIN_BUCKET, client_id(request))


def _user_key(user_id: int) -> str:
    return f"user:{user_id}"


def user_login_blocked(user_id: int) -> bool:
    s = settings.web
    if not s.rate_limit_enabled:
        return False
    return _count(LOGIN_BUCKET, _user_key(user_id)) >= s.login_max_failures


def record_failed_user_login(user_id: int) -> None:
    hit(LOGIN_BUCKET, _user_key(user_id), settings.web.login_failure_window_s)


def clear_failed_user_logins(user_id: int) -> None:
    _clear(LOGIN_BUCKET, _user_key(user_id))


def webhook_blocked(request: Request) -> bool:
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
