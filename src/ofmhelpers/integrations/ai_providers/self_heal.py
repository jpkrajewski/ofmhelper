"""Self-heal: one policy for every provider, for the calls whose answer can be
lost after the provider acted on them -- a submit (paid) and a result
download (paid for already).

A provider only declares, on the `Provider` interface, how a lost submit is
found again; nothing here names one:

- `idempotent_submit` (Higgsfield, Elyum): a resend with the same key answers
  the request already made, so a submit whose answer was lost is sent again;
- `callback_url` / `callback_task_id` / `callback_signed` (kie.ai): the
  provider calls back when the request ends. The submit is noted first
  (`tasks.remember_sent`); a lost answer keeps the job running until the
  callback names the request (`heard`, behind `POST /events/ai/<provider>/<job
  key>`), and a callback after the job gave up hands it to recovery
  (`features/jobs/recovery.py`), which re-runs the job resuming it -- never
  paying twice. A noted submit is never sent again;
- neither (RunningHub: no idempotency key, no webhook, no task list on its
  model API): a submit is never resent, so never paid twice, and a lost answer
  fails the job.

Every provider: a rate-limited submit is resent (nothing was made); a submit
that never left (connection refused, DNS, connect timeout:
`ProviderUnavailableError.maybe_made` False) is never waited on; a result
download failing transiently is fetched again.

Why: prod 2026-11-05, kie.ai made and billed a Wan 3.0 clip 2 s after our
createTask gave up; it was found only on kie.ai's own site. User rule: nobody
ever checks a provider's site.
"""

import random
import re
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.base import Provider, provider_classes
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.ai_providers.spec import ModelSpec, OutputKind, Payload
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

# Each resend wait is stretched by up to this much, so the runs one burst
# refused do not all come back in the same second.
_JITTER = 0.25
# Under /events/, which is public and outside the write rate limit.
CALLBACK_PATH = "/events/ai"
# A job id, then `:<part>` for a job that makes several requests.
_JOB_KEY = re.compile(r"^(?P<job>[A-Za-z0-9-]{1,64})(?::(?P<part>\d{1,4}))?$")


def _sleep(seconds: float) -> None:
    """Every wait here (tests replace it)."""
    time.sleep(seconds)


def job_key(job_id: str, part: int) -> str:
    """What a request is known by at the provider: its idempotency key and
    the end of its callback address. Part 0 is the job id alone."""
    return job_id if part == 0 else f"{job_id}:{part}"


# --- submit --------------------------------------------------------------------


def _submit(provider: Provider, spec: ModelSpec, payload: Payload, key: str) -> str:
    """`provider.submit`, sent again only where that cannot pay twice: after a
    rate limit (the provider created nothing), or after any lost answer on a
    provider whose submit is idempotent. Once per `rate_limit_waits_s`
    entry, never waiting more than `rate_limit_max_wait_s` in all. Elsewhere
    a timeout or a 5xx may have created a paid task: raised at once."""
    s = settings.aigen
    waits = iter(s.rate_limit_waits_s)
    left = s.rate_limit_max_wait_s
    while True:
        try:
            return provider.submit(spec, payload, idempotency_key=key)
        except ProviderUnavailableError as exc:
            limited = isinstance(exc, ProviderRateLimitError)
            if not (limited or provider.idempotent_submit):
                raise
            backoff = next(waits, None)
            if backoff is None:
                raise
            wait_s = (
                exc.retry_after_s if isinstance(exc, ProviderRateLimitError) else None
            )
            if wait_s is None:
                spread = random.uniform(1, 1 + _JITTER)  # noqa: S311 -- spread, not crypto
                wait_s = backoff * spread
            if wait_s > left:
                raise
            left -= wait_s
            logger.warning(
                "%s refused the submit of %s (%s), sending it again in %.0f s",
                provider.label,
                key,
                exc,
                wait_s,
            )
            _sleep(wait_s)


def _await_callback(
    job_id: str, part: int, kind: OutputKind, since: float
) -> str | None:
    """The id of a submit whose answer was lost, once the provider's callback
    names it (`heard` -> `tasks.found`); None once a generation's wait from
    `since` (when it was sent) is over. The job keeps running meanwhile."""
    s = settings.aigen
    timeout_s = s.video_poll_timeout_s if kind == "video" else s.poll_timeout_s
    while time.time() < since + timeout_s:
        task = tasks.recall(job_id, part)
        if task is not None:
            return task.task_id
        _sleep(s.poll_max_interval_s)
    return None


def _not_answered(provider: Provider) -> ProviderUnavailableError:
    msg = (
        f"{provider.label} timed out and has not reported the request yet. It is "
        f"not sent again (it may be made and paid): this job finishes by itself "
        f"once {provider.label} reports it."
    )
    return ProviderUnavailableError(msg)


def _lost_task(provider: Provider, noted: tasks.ProviderTask, kind: OutputKind) -> str:
    """A noted submit whose answer never came: it may be made and paid, so it
    is never sent again -- its callback names it (a long clone re-run by
    recovery would otherwise pay for the piece twice)."""
    found = _await_callback(noted.job_id, noted.part, kind, noted.created_at)
    if found is None:
        raise _not_answered(provider)
    logger.info(
        "job %s: %s named the lost task %s", noted.job_id, provider.label, found
    )
    return found


def _submit_noted(
    provider: Provider, spec: ModelSpec, body: Payload, job_id: str, part: int
) -> str:
    key = job_key(job_id, part)
    if not provider.callback_url(key):
        return _submit(provider, spec, body, key)
    noted = tasks.ProviderTask(
        job_id=job_id,
        part=part,
        provider=provider.name,
        model=spec.key,
        task_id="",
        api_key=provider.api_key,
    )
    tasks.remember_sent(noted)
    try:
        task_id = _submit(provider, spec, body, key)
    except ProviderRateLimitError:
        tasks.forget_sent(job_id, part)
        raise
    except ProviderUnavailableError as lost:
        if not lost.maybe_made:
            tasks.forget_sent(job_id, part)
            raise
        logger.warning(
            "%s did not answer the submit of %s (%s): waiting for its callback",
            provider.label,
            key,
            lost,
        )
        # Noted still when nothing came: a later callback finishes the job
        # through recovery.
        return _lost_task(provider, noted, spec.kind)
    except Exception:
        tasks.forget_sent(job_id, part)
        raise
    tasks.forget_sent(job_id, part)
    return task_id


def new_task(
    provider: Provider,
    spec: ModelSpec,
    payload: Payload | Callable[[], Payload],
    job_id: str,
    part: int,
) -> str:
    """The provider's id for a request the job has not remembered yet: the
    one an earlier submit made (noted, its answer lost: waited for, never
    sent again, its references never uploaded again), else a new submit.
    `payload` may be a function: it uploads the references, so it runs only
    when a request is really sent."""
    noted = tasks.sent(job_id, part)
    if (
        noted is not None
        and noted.provider == provider.name
        and noted.model == spec.key
    ):
        return _lost_task(provider, noted, spec.kind)
    body = payload() if callable(payload) else payload
    return _submit_noted(provider, spec, body, job_id, part)


# --- result download -------------------------------------------------------------


def download(provider: Provider, url: str, dest: Path) -> Path:
    """`provider.download`, tried again after each `download_retry_waits_s`
    while it fails transiently: the result is paid for and its URL stays
    valid for hours, so one reset or CDN 5xx must not cost the file."""
    for wait_s in settings.aigen.download_retry_waits_s:
        try:
            return provider.download(url, dest)
        except ProviderUnavailableError:
            logger.warning(
                "download of %s failed, trying again in %.0f s",
                url,
                wait_s,
                exc_info=True,
            )
        _sleep(wait_s)
    return provider.download(url, dest)


# --- callbacks -------------------------------------------------------------------


class Heard(StrEnum):
    FOUND = "found"
    IGNORED = "ignored"
    REFUSED = "refused"
    UNKNOWN = "unknown"


def callback_url(provider: str, key: str) -> str:
    """This server's address for `provider`'s callback about `key`; empty
    unless APP_BASE_URL is public HTTPS (dev: no callback, nothing waits)."""
    base = settings.web.base_url
    if not base.startswith("https://"):
        return ""
    return f"{base.rstrip('/')}{CALLBACK_PATH}/{provider}/{key}"


def _ours(cls: type[Provider], api_key: str, task_id: str, key: str) -> bool:
    """An unsigned callback's task, checked with the job's own key: a made-up
    id is refused as not on our account. A provider too slow to say is taken
    at its word -- the run's own poll checks the task anyway -- rather than
    answered with an error it may never retry (kie.ai's Veo page: three
    tries, then no more callbacks)."""
    try:
        cls(api_key).status(task_id)
    except ProviderError as exc:
        if exc.retryable:
            logger.warning(
                "%s callback for job %s: %s not checked now", cls.name, key, task_id
            )
            return True
        logger.warning(
            "%s callback for job %s names %s, not ours", cls.name, key, task_id
        )
        return False
    return True


def heard(provider: str, key: str, raw: bytes, headers: Mapping[str, str]) -> Heard:
    """A provider's callback about `key`. The normal case -- a request the
    job knows -- changes nothing; one naming the request of a noted submit
    remembers it as the job's task."""
    cls = provider_classes().get(provider)
    if cls is None:
        return Heard.UNKNOWN
    task_id = cls.callback_task_id(raw, headers)
    if task_id is None:
        return Heard.REFUSED
    match = _JOB_KEY.match(key)
    if match is None:
        return Heard.IGNORED
    job_id, part = match["job"], int(match["part"] or 0)
    if tasks.recall(job_id, part) is not None:
        return Heard.IGNORED
    noted = tasks.sent(job_id, part)
    if noted is None or noted.provider != provider:
        return Heard.IGNORED
    if not cls.callback_signed() and not _ours(cls, noted.api_key, task_id, key):
        return Heard.IGNORED
    tasks.found(job_id, part, task_id)
    logger.info("%s callback: job %s made task %s, answer lost", provider, key, task_id)
    return Heard.FOUND
