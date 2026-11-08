"""The aggregator, on the `events` queue: an event emitted (bus.py) goes to every
sender that is set up (`Sender.targets()`) and not switched off
(`OFM_EVENTS_SENDERS_OFF`), one `deliver` job per (sender, target).

A delivery:
- skips when its sender was switched off since, or when its `key` already
  reached that target (`events:sent:*`, `OFM_EVENTS_KEY_TTL_S`): a producer that
  runs twice, or a webhook delivered twice, sends one message. The key counts
  only once a send went through, so a send that failed may be emitted again;
- waits out its sender's pace for that target (`Sender.pace_s`, `events:pace:*`)
  instead of being refused for flooding, and a service's own "retry after" up to
  `OFM_EVENTS_MAX_WAIT_S`, in place (keeping the order);
- gives up at once when trying again cannot help (`SendError.permanent`: the
  chat is gone, the bot was removed); anything else is tried again by RQ
  (`OFM_EVENTS_RETRY_WAITS_S`), on its own, behind what is queued by then. A send
  that fails every try stays in RQ's failed registry `OFM_EVENTS_FAILED_KEEP_S`
  (`rq requeue --queue events --all` sends them again).

A send that timed out after the service took it can arrive twice: at least once,
never silently lost.

An event that `replaces` a name deletes the message sent before under that name,
in that target, once it is delivered itself (`events:slot:*`, 48 h): the chat keeps
only the newest. Nothing sent under it, nothing deleted.

Logfire: `event {event} to {sender}` per try with `outcome` sent / skipped /
retry / gave_up, never its target (a chat id, a webhook URL); RQ's own log lines
lose the job's arguments (log.py `_HideJobArguments`). Lost messages:
`attributes->>'outcome' = 'gave_up'`.
"""

import hashlib
import time
from typing import Any

import logfire
from rq import Retry, get_current_job

from ofmhelpers.cache import JobQueue, QueueName, get_redis
from ofmhelpers.config import settings
from ofmhelpers.integrations.senders import SendError, senders
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.events import Event
lazy from ofmhelpers.integrations.senders import Sender

logger = get_logger(__name__)

# Tests replace it: no real waits.
_sleep = time.sleep
# How long a sent message can still be replaced (Telegram deletes a message only
# while it is under 48 h old).
_SLOT_S = 48 * 3600


def _off() -> set[str]:
    off = set(settings.events.senders_off_list())
    unknown = off - set(senders())
    if unknown:
        logger.warning("OFM_EVENTS_SENDERS_OFF names no sender: %s", sorted(unknown))
    return off


def _retry() -> Retry | None:
    waits = settings.events.retry_waits_s
    return Retry(max=len(waits), interval=waits) if waits else None


def _queue_delivery(sender_name: str, target: str, event: dict[str, Any]) -> None:
    JobQueue(QueueName.EVENTS).put(
        deliver,
        sender_name,
        target,
        event,
        job_timeout=settings.events.send_timeout_s,
        retry=_retry(),
        failure_ttl=settings.events.failed_keep_s,
    )


def dispatch(event: dict[str, Any]) -> None:
    """RQ job: `event` (an `Event` as JSON) to each sender's targets."""
    found = Event.model_validate(event)
    off = _off()
    for sender in senders().values():
        if sender.name in off:
            continue
        for target in sender.targets():
            try:
                _queue_delivery(sender.name, target, event)
            except Exception:
                logger.exception(
                    "event %s: %s delivery failed or not queued",
                    found.name,
                    sender.name,
                )


def deliver_to(sender_name: str, target: str, event: Event) -> None:
    """`event` to that one target only (an answer to the chat that asked), the
    way every delivery goes: paced, retried, skipped while its sender is off.
    Raises when it could not be queued."""
    _queue_delivery(sender_name, target, event.model_dump(mode="json"))


def _digest(target: str) -> str:
    """A target in a Redis key name without the target itself."""
    return hashlib.sha256(target.encode()).hexdigest()[:16]


def _sent_key(found: Event, sender: Sender, target: str) -> str | None:
    if found.key is None:
        return None
    return f"events:sent:{found.key}:{sender.name}:{_digest(target)}"


def _wait_pace(sender: Sender, target: str) -> str:
    """Sleeps until `target` may get the next send; the key that remembers it."""
    key = f"events:pace:{sender.name}:{_digest(target)}"
    last = get_redis().get(key)
    if last is not None:
        wait = float(last) + sender.pace_s(target) - time.time()
        if wait > 0:
            _sleep(wait)
    return key


def _send(sender: Sender, target: str, found: Event) -> str | None:
    """One send; a short "retry after" is waited out and tried once more."""
    try:
        return sender.send(target, found)
    except SendError as exc:
        wait = exc.retry_after
        if wait is None or wait > settings.events.max_wait_s:
            raise
        _sleep(wait)
        return sender.send(target, found)


def _delete(sender: Sender, target: str, found: Event, ref: str) -> None:
    try:
        sender.delete(target, ref)
    except SendError:
        logger.warning(
            "event %s: %s could not delete the one before",
            found.name,
            sender.name,
            exc_info=True,
        )


def _replace(sender: Sender, target: str, found: Event, ref: str | None) -> None:
    """The message sent before under `found.replaces` deleted, this one remembered
    in its place (`<made at>|<ref>`). One made before what is there already (a late
    retry, a requeue) is deleted itself instead: the chat keeps the newest. One
    that cannot be deleted (too old, deleted by hand) stays."""
    if found.replaces is None or ref is None:
        return
    slot = f"events:slot:{found.replaces}:{sender.name}:{_digest(target)}"
    redis = get_redis()
    held = redis.get(slot)
    text = held.decode() if isinstance(held, bytes) else ""
    held_at, _, held_ref = text.rpartition("|")
    if held_ref and held_at and float(held_at) > found.at:
        _delete(sender, target, found, ref)
        return
    redis.set(slot, f"{found.at}|{ref}", ex=_SLOT_S)
    if held_ref and held_ref != ref:
        _delete(sender, target, found, held_ref)


def listening() -> bool:
    """Whether any sender that is on has somewhere to send: none, and a producer
    with costly reads (the daily report, the unanswered list) does nothing."""
    off = set(settings.events.senders_off_list())
    return any(s.targets() for s in senders().values() if s.name not in off)


def _last_try() -> bool:
    job = get_current_job()
    return job is None or not job.retries_left


def deliver(sender_name: str, target: str, event: dict[str, Any]) -> None:
    """RQ job: one send. Raises the sender's `SendError` so RQ tries again."""
    found = Event.model_validate(event)
    sender = senders().get(sender_name)
    if sender is None:
        logger.warning("event %s: no sender %r", found.name, sender_name)
        return
    with logfire.span(
        "event {event} to {sender}", event=found.name, sender=sender_name
    ) as span:
        sent_key = _sent_key(found, sender, target)
        if sender_name in _off() or (sent_key and get_redis().exists(sent_key)):
            span.set_attribute("outcome", "skipped")
            return
        pace_key = _wait_pace(sender, target)
        try:
            ref = _send(sender, target, found)
        except SendError as exc:
            gave_up = exc.permanent or _last_try()
            span.set_attribute("outcome", "gave_up" if gave_up else "retry")
            if exc.permanent:
                # The reason in the line itself: what Logfire's message column shows.
                logger.exception(
                    "event %s: %s gave up: %s",
                    found.name,
                    sender_name,
                    exc,  # noqa: TRY401
                )
                return
            log = logger.error if gave_up else logger.warning
            log("event %s: %s failed: %s", found.name, sender_name, exc)
            raise
        redis = get_redis()
        redis.set(pace_key, str(time.time()), ex=int(sender.pace_s(target)) + 1)
        if sent_key:
            redis.set(sent_key, "1", ex=settings.events.key_ttl_s)
        span.set_attribute("outcome", "sent")
        _replace(sender, target, found, ref)
    logger.info("event %s sent by %s", found.name, sender_name)
