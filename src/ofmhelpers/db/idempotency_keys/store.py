from datetime import UTC, datetime, timedelta
from functools import lru_cache

lazy from pydantic import JsonValue

from ofmhelpers.config import settings
from ofmhelpers.db.idempotency_keys.repository import IdempotencyKeyRepository
lazy from ofmhelpers.db.idempotency_keys.schema import Asked, Claimed, Held


@lru_cache(maxsize=1)
def _repository() -> IdempotencyKeyRepository:
    return IdempotencyKeyRepository()


def claim(asked: Asked) -> Claimed:
    stale_before = datetime.now(UTC) - timedelta(seconds=settings.idempotency.stale_s)
    return _repository().claim(asked, stale_before)


def finish(held: Held, status: int, body: JsonValue) -> None:
    _repository().finish(held, status, body)


def release(held: Held) -> None:
    _repository().release(held)


def remove_older(before: datetime) -> int:
    return _repository().remove_older(before)
