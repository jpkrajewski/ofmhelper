lazy from datetime import datetime

from sqlalchemy import ColumnElement, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
lazy from pydantic import JsonValue

from ofmhelpers.db.cached_repository import CachedRepository
from ofmhelpers.db.idempotency_keys.schema import Asked, Claimed, Held, Outcome
from ofmhelpers.db.models import IdempotencyKeyRow
from ofmhelpers.db.session import session_scope

STARTED = "started"
DONE = "done"


def _same(asked: Asked) -> list[ColumnElement[bool]]:
    row = IdempotencyKeyRow
    return [row.user_id == asked.user_id, row.key == asked.key]


def _still_mine(held: Held) -> list[ColumnElement[bool]]:
    row = IdempotencyKeyRow
    return [
        *_same(held.asked),
        row.status == STARTED,
        row.created_at == held.claimed_at,
    ]


class IdempotencyKeyRepository(CachedRepository):
    cache_namespace = "idempotency_key"

    def claim(self, asked: Asked, stale_before: datetime) -> Claimed:
        row = IdempotencyKeyRow
        fresh = {"path": asked.path, "fingerprint": asked.fingerprint}
        with session_scope() as s:
            taken = s.execute(
                insert(row)
                .values(user_id=asked.user_id, key=asked.key, status=STARTED, **fresh)
                .on_conflict_do_nothing(index_elements=[row.user_id, row.key])
                .returning(row.created_at)
            ).scalar_one_or_none()
            if taken is not None:
                return Claimed(
                    outcome=Outcome.NEW, held=Held(asked=asked, claimed_at=taken)
                )
            found = s.execute(select(row).where(*_same(asked))).scalar_one_or_none()
            if found is None:
                return Claimed(outcome=Outcome.BUSY)
            if found.path != asked.path or found.fingerprint != asked.fingerprint:
                return Claimed(outcome=Outcome.MISMATCH)
            if found.status == STARTED:
                gone = found.created_at < stale_before
                return Claimed(outcome=Outcome.ABANDONED if gone else Outcome.BUSY)
            return Claimed(
                outcome=Outcome.REPLAY,
                response_status=found.response_status,
                response_body=found.response_body,
            )

    def finish(self, held: Held, status: int, body: JsonValue) -> None:
        with session_scope() as s:
            s.execute(
                update(IdempotencyKeyRow)
                .where(*_still_mine(held))
                .values(
                    status=DONE,
                    response_status=status,
                    response_body=body,
                    finished_at=func.now(),
                )
            )

    def release(self, held: Held) -> None:
        with session_scope() as s:
            s.execute(delete(IdempotencyKeyRow).where(*_still_mine(held)))

    def remove_older(self, before: datetime) -> int:
        with session_scope() as s:
            gone = s.execute(
                delete(IdempotencyKeyRow)
                .where(IdempotencyKeyRow.created_at < before)
                .returning(IdempotencyKeyRow.key)
            )
            return len(gone.all())
