import hashlib
import time
from typing import Any

import logfire
from pydantic import BaseModel, SkipValidation
from rq import Retry, get_current_job

from ofmhelpers.cache import JobQueue, QueueName, get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.event_targets import store as event_targets
from ofmhelpers.db.users import store as users
from ofmhelpers.integrations.senders import SendError, senders
from ofmhelpers.integrations.senders.schema import Event
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.senders import Sender

logger = get_logger(__name__)

_sleep = time.sleep
_SLOT_S = 48 * 3600


def _off() -> set[str]:
    if not settings.events.sending(settings.infra.production):
        return set(senders().keys())
    off = set(settings.events.senders_off_list())
    unknown = off - set(senders().keys())
    if unknown:
        logger.warning("OFM_EVENTS_SENDERS_OFF names no sender: %s", sorted(unknown))
    return off


def _retry() -> Retry | None:
    waits = settings.events.retry_waits_s
    return Retry(max=len(waits), interval=waits) if waits else None


def _targets(sender: Sender, event: Event) -> list[str]:
    if event.owner_id is None:
        return sender.targets()
    owner = users.get_user(event.owner_id)
    if owner is None or not owner.active:
        logger.info("event %s: user %s is not active", event.name, event.owner_id)
        return []
    own = event_targets.of(event.owner_id, sender.name)
    if own:
        return [own]
    if owner.is_admin:
        return sender.targets()
    logger.info(
        "event %s: user %s linked no %s", event.name, event.owner_id, sender.name
    )
    return []


def dispatch(event: dict[str, Any]) -> None:
    found = Event.model_validate(event)
    off = _off()
    for sender in senders().values():
        if sender.name in off:
            continue
        for target in _targets(sender, found):
            try:
                deliver_to(sender.name, target, found)
            except Exception:
                logger.exception(
                    "event %s: %s delivery failed or not queued",
                    found.name,
                    sender.name,
                )


def deliver_to(sender_name: str, target: str, event: Event) -> None:
    JobQueue(name=QueueName.EVENTS).put(
        deliver,
        (sender_name, target, event.model_dump(mode="json")),
        job_timeout=settings.events.send_timeout_s,
        retry=_retry(),
        failure_ttl=settings.events.failed_keep_s,
    )


def _digest(target: str) -> str:
    return hashlib.sha256(target.encode()).hexdigest()[:16]


class Delivery(BaseModel, frozen=True, arbitrary_types_allowed=True):
    sender: SkipValidation[Sender]
    target: str
    found: Event

    def _sent_key(self) -> str | None:
        if self.found.key is None:
            return None
        return f"events:sent:{self.found.key}:{self.sender.name}:{_digest(self.target)}"

    def _wait_pace(self) -> str:
        key = f"events:pace:{self.sender.name}:{_digest(self.target)}"
        last = get_redis().get(key)
        if last is not None:
            wait = float(last) + self.sender.pace_s(self.target) - time.time()
            if wait > 0:
                _sleep(wait)
        return key

    def _send(self) -> str | None:
        try:
            return self.sender.send(self.target, self.found)
        except SendError as exc:
            wait = exc.retry_after
            if wait is None or wait > settings.events.max_wait_s:
                raise
            _sleep(wait)
            return self.sender.send(self.target, self.found)

    def _delete(self, ref: str) -> None:
        try:
            self.sender.delete(self.target, ref)
        except SendError:
            logger.warning(
                "event %s: %s could not delete the one before",
                self.found.name,
                self.sender.name,
                exc_info=True,
            )

    def _replace(self, ref: str | None) -> None:
        found = self.found
        if found.replaces is None or ref is None:
            return
        slot = f"events:slot:{found.replaces}:{self.sender.name}:{_digest(self.target)}"
        redis = get_redis()
        held = redis.get(slot)
        text = held.decode() if isinstance(held, bytes) else ""
        held_at, _, held_ref = text.rpartition("|")
        if held_ref and held_at and float(held_at) > found.at:
            self._delete(ref)
            return
        redis.set(slot, f"{found.at}|{ref}", ex=_SLOT_S)
        if held_ref and held_ref != ref:
            self._delete(held_ref)

    def run(self) -> None:
        found, sender, target = self.found, self.sender, self.target
        with logfire.span(
            "event {event} to {sender}", event=found.name, sender=sender.name
        ) as span:
            sent_key = self._sent_key()
            if sender.name in _off() or (sent_key and get_redis().exists(sent_key)):
                span.set_attribute("outcome", "skipped")
                return
            pace_key = self._wait_pace()
            try:
                ref = self._send()
            except SendError as exc:
                gave_up = exc.permanent or _last_try()
                span.set_attribute("outcome", "gave_up" if gave_up else "retry")
                if exc.permanent:
                    logger.exception(
                        "event %s: %s gave up: %s",
                        found.name,
                        sender.name,
                        exc,  # noqa: TRY401
                    )
                    return
                log = logger.error if gave_up else logger.warning
                log("event %s: %s failed: %s", found.name, sender.name, exc)
                raise
            redis = get_redis()
            redis.set(pace_key, str(time.time()), ex=int(sender.pace_s(target)) + 1)
            if sent_key:
                redis.set(sent_key, "1", ex=settings.events.key_ttl_s)
            span.set_attribute("outcome", "sent")
            self._replace(ref)
        logger.info("event %s sent by %s", found.name, sender.name)


def listening() -> bool:
    off = _off()
    return any(s.targets() for s in senders().values() if s.name not in off)


def _last_try() -> bool:
    job = get_current_job()
    return job is None or not job.retries_left


def deliver(sender_name: str, target: str, event: dict[str, Any]) -> None:
    found = Event.model_validate(event)
    sender = senders().get(sender_name)
    if sender is None:
        logger.warning("event %s: no sender %r", found.name, sender_name)
        return
    Delivery(sender=sender, target=target, found=found).run()
