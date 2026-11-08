"""A fan's chat looked at again `OFM_CHATTING_UNANSWERED_AFTER_S` (5 min) after
each of their messages, however it came in (webhook or sync), so "Chats
unanswered" goes out when the 5 minutes are up, not at the next 10-minute look
(prod 2026-11-06: three fans who wrote at 18:50-18:55 were told at 19:07).

Redis holds:
- the rechecks due: one sorted set, member a `Recheck` as JSON, score = when it
  is due; each taken by the one caller whose `ZREM` removed it. One whose chat
  could not be read is put back for the next poll (`again`), until it is older
  than a look plus the 5 minutes: the look has it from there.
- a mark per wait already told (`OpenWait.id`): a fan writing three times in a
  row is told once, and a wait the look named is not named again by its recheck.
- one lock while a list is made (`one_teller`): a look and a recheck at once
  would both read, both find the fan untold and both send. Renewed while the
  list is made, however long Stacked's 429s make it (one took over 120 s, the
  lock's life then, and two lists went out a second apart: prod 2026-11-07
  18:07); a process killed mid-list leaves it at most `_TELL_LOCK_S`. A caller
  that cannot take it within a few seconds makes no list: a recheck is due
  again at the next poll, a look leaves it to the next look.

Redis down: logged, nothing planned or marked, the list made unlocked; the
10-minute look still tells. A message older than the 5 minutes plus a look is
not planned: the look has it (a first read of a chat's history would otherwise
plan thousands)."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, ValidationError
from redis.exceptions import LockError, RedisError

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from redis.lock import Lock

    from ofmhelpers.schemas.chatting import ChatPlatform, ChatRead, OpenWait

logger = get_logger(__name__)

_DUE = "chatting:wait-rechecks"
_TOLD = "chatting:wait-told:"
_TELLING = "lock:chatting:telling"
# Outlives any wait someone still acts on.
_TOLD_TTL_S = 2 * 24 * 60 * 60
# A list is a read of each model with fans waiting (seconds each, minutes when
# Stacked answers 429) and one emit: the lock lives this long and is renewed
# every third of it while the list is made (a killed holder's lapses).
_TELL_LOCK_S = 120
# How long a second caller waits for the list being made before it skips.
_TELL_WAIT_S = 5.0
# Due just past the 5 minutes: the wait counts only once strictly older.
_MARGIN_S = 1


class Recheck(BaseModel):
    """One fan message's recheck; its JSON is the sorted set's member."""

    model_config = ConfigDict(frozen=True)

    platform: str
    channel_id: str
    message_id: str
    at: float

    @property
    def chat(self) -> tuple[str, str]:
        return (self.platform, self.channel_id)


def _oldest(now: float) -> float:
    """A message older than this is the look's."""
    config = settings.chatting
    return now - config.unanswered_after_s - config.wait_events_check_s


def plan(platform: ChatPlatform, read: ChatRead, now: float | None = None) -> None:
    """A recheck for each fan message of `read`, due when it is 5 min old."""
    if platform.value in settings.chatting.events_off():
        return
    oldest = _oldest(time.time() if now is None else now)
    after = settings.chatting.unanswered_after_s + _MARGIN_S
    due = {
        Recheck(
            platform=platform.value,
            channel_id=read.channel_id,
            message_id=event.message_id,
            at=event.at,
        ).model_dump_json(): event.at + after
        for event in read.events
        if event.from_fan and event.at >= oldest
    }
    if not due:
        return
    try:
        get_redis().zadd(_DUE, due, nx=True)
    except RedisError:
        logger.warning(
            "chat rechecks of %s not planned", read.channel_id, exc_info=True
        )


def take_due(now: float) -> list[Recheck]:
    """Every recheck due by `now`, each taken once, whichever process asks."""
    try:
        redis = get_redis()
        members = cast("list[bytes]", redis.zrangebyscore(_DUE, "-inf", now))
        taken = [m for m in members if redis.zrem(_DUE, m)]
    except RedisError:
        logger.warning("chat rechecks not read", exc_info=True)
        return []
    found = []
    for member in taken:
        try:
            found.append(Recheck.model_validate_json(member))
        except ValidationError:
            logger.warning("chat recheck %r unreadable, dropped", member, exc_info=True)
    return found


def again(rechecks: Iterable[Recheck], now: float) -> None:
    """`rechecks` due again at the next poll, but those the look has by now."""
    oldest = _oldest(now)
    due = now + settings.chatting.wait_recheck_poll_s
    kept = {r.model_dump_json(): due for r in rechecks if r.at >= oldest}
    if not kept:
        return
    try:
        get_redis().zadd(_DUE, kept)
    except RedisError:
        logger.warning("%d chat rechecks not put back", len(kept), exc_info=True)


@contextmanager
def one_teller() -> Generator[bool]:
    """One list made at a time, across processes. True: this caller makes it (the
    lock held and renewed until the list is made, or Redis down: unlocked,
    logged). False: another list is being made, so this one is skipped."""
    # Not thread-local: the renewing thread holds the same token.
    lock = get_redis().lock(
        _TELLING,
        timeout=_TELL_LOCK_S,
        blocking_timeout=_TELL_WAIT_S,
        thread_local=False,
    )
    try:
        held = bool(lock.acquire())
    except RedisError:
        logger.warning("chat telling lock unavailable", exc_info=True)
        held = False
        unlocked = True
    else:
        unlocked = False
    if not held:
        if not unlocked:
            logger.info("chat list already being made: this one skipped")
        yield unlocked
        return
    done = threading.Event()
    renewing = threading.Thread(
        target=_renew, args=(lock, done), name="chat-telling-lock", daemon=True
    )
    renewing.start()
    try:
        yield True
    finally:
        done.set()
        renewing.join()
        try:
            lock.release()
        except (RedisError, LockError):
            logger.warning("chat telling lock not released", exc_info=True)


def _renew(lock: Lock, done: threading.Event) -> None:
    """`lock` back to its full life every third of it, until `done`."""
    while not done.wait(_TELL_LOCK_S / 3):
        try:
            lock.reacquire()
        except (RedisError, LockError):
            logger.warning("chat telling lock not renewed", exc_info=True)
            return


def untold(waits: list[OpenWait]) -> list[OpenWait]:
    """Those of `waits` nobody was told about yet (Redis down: all of them)."""
    if not waits:
        return []
    try:
        marks = get_redis().mget([_TOLD + w.id for w in waits])
    except RedisError:
        logger.warning("chat told marks not read", exc_info=True)
        return waits
    return [w for w, mark in zip(waits, marks, strict=True) if mark is None]


def mark_told(waits: list[OpenWait]) -> None:
    if not waits:
        return
    try:
        pipe = get_redis().pipeline()
        for wait in waits:
            pipe.set(_TOLD + wait.id, "1", ex=_TOLD_TTL_S)
        pipe.execute()
    except RedisError:
        logger.warning("chat told marks not saved", exc_info=True)
