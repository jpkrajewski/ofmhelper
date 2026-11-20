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
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.ai_providers.spec import (
    ModelSpec,
    OutputKind,
    Payload,
)

logger = get_logger(__name__)

_JITTER = 0.25
CALLBACK_PATH = "/events/ai"
_JOB_KEY = re.compile(r"^(?P<job>[A-Za-z0-9-]{1,64})(?::(?P<part>\d{1,4}))?$")


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def job_key(job_id: str, part: int) -> str:
    return job_id if part == 0 else f"{job_id}:{part}"


def _submit(provider: Provider, spec: ModelSpec, payload: Payload, key: str) -> str:
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
                spread = random.uniform(1, 1 + _JITTER)  # noqa: S311
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
                exc_info=True,
            )
            _sleep(wait_s)


def _await_callback(
    job_id: str, part: int, kind: OutputKind, since: float
) -> str | None:
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
            exc_info=True,
        )
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
    noted = tasks.sent(job_id, part)
    if (
        noted is not None
        and noted.provider == provider.name
        and noted.model == spec.key
    ):
        return _lost_task(provider, noted, spec.kind)
    body = payload() if callable(payload) else payload
    return _submit_noted(provider, spec, body, job_id, part)


def download(provider: Provider, url: str, dest: Path) -> Path:
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


class Heard(StrEnum):
    FOUND = "found"
    IGNORED = "ignored"
    REFUSED = "refused"
    UNKNOWN = "unknown"


def callback_url(provider: str, key: str) -> str:
    base = settings.web.base_url
    if not base.startswith("https://"):
        return ""
    return f"{base.rstrip('/')}{CALLBACK_PATH}/{provider}/{key}"


def _ours(cls: type[Provider], api_key: str, task_id: str, key: str) -> bool:
    try:
        cls(api_key).status(task_id)
    except ProviderError as exc:
        if exc.retryable:
            logger.warning(
                "%s callback for job %s: %s not checked now",
                cls.name,
                key,
                task_id,
                exc_info=True,
            )
            return True
        logger.warning(
            "%s callback for job %s names %s, not ours",
            cls.name,
            key,
            task_id,
            exc_info=True,
        )
        return False
    return True


def heard(provider: str, key: str, raw: bytes, headers: Mapping[str, str]) -> Heard:
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
    if not _ours(cls, noted.api_key, task_id, key):
        return Heard.IGNORED
    tasks.found(job_id, part, task_id)
    logger.info("%s callback: job %s made task %s, answer lost", provider, key, task_id)
    return Heard.FOUND
