# Copyright (C) 2026 jpkrajewski

import hashlib
import json
from datetime import UTC, datetime, timedelta

lazy from pydantic import JsonValue

from ofmhelpers.config import settings
from ofmhelpers.db.idempotency_keys import store
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.idempotency_keys.schema import Asked, Claimed, Held

logger = get_logger(__name__)


def fingerprint(fields: list[str]) -> str:
    return hashlib.sha256(json.dumps(sorted(fields)).encode()).hexdigest()


def claim(asked: Asked) -> Claimed:
    return store.claim(asked)


def finish(held: Held, status: int, body: JsonValue) -> None:
    store.finish(held, status, body)


def release(held: Held) -> None:
    store.release(held)


def remove_old() -> int:
    before = datetime.now(UTC) - timedelta(seconds=settings.idempotency.keep_s)
    gone = store.remove_older(before)
    logger.info("idempotency: %d keys older than %s removed", gone, before)
    return gone
