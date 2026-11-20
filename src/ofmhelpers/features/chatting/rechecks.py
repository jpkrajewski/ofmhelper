import threading
import time
from contextlib import contextmanager
from typing import cast
lazy from collections.abc import Generator, Iterable

from pydantic import BaseModel, ConfigDict, ValidationError
from redis.exceptions import LockError, RedisError
lazy from redis.lock import Lock

from ofmhelpers.cache.redis import get_redis
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.chats.schema import ChatPlatform, ChatRead, OpenWait

logger = get_logger(__name__)

_DUE = "chatting:wait-rechecks"
_TOLD = "chatting:wait-told:"
_TELLING = "lock:chatting:telling"
_TOLD_TTL_S = 2 * 24 * 60 * 60
_TELL_LOCK_S = 120
_TELL_WAIT_S = 5.0
_MARGIN_S = 1


class ChatKey(BaseModel, frozen=True):
    platform: str
    channel_id: str


class Recheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    platform: str
    channel_id: str
    message_id: str
    at: float

    @property
    def chat(self) -> ChatKey:
        return ChatKey(platform=self.platform, channel_id=self.channel_id)


def _oldest(now: float) -> float:
    config = settings.chatting
    return now - config.unanswered_after_s - config.wait_events_check_s


def plan(platform: ChatPlatform, read: ChatRead, now: float | None = None) -> None:
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
        except RedisError, LockError:
            logger.warning("chat telling lock not released", exc_info=True)


def _renew(lock: Lock, done: threading.Event) -> None:
    while not done.wait(_TELL_LOCK_S / 3):
        try:
            lock.reacquire()
        except RedisError, LockError:
            logger.warning("chat telling lock not renewed", exc_info=True)
            return


def untold(waits: list[OpenWait]) -> list[OpenWait]:
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
