"""One generation on any provider: submit (or resume), wait, save the result.

The only place the request lifecycle is written. A provider adapter answers
single calls; this decides what to do with each answer:

- a job that already has an accepted request (`tasks.recall`) resumes it
  instead of submitting again -- a re-run never pays twice;
- a submit is sent again only when it was rate-limited
  (`ProviderRateLimitError`: nothing was created), a bounded few times;
  any other submit failure may have been paid and is never resent;
- a status check that fails transiently is retried until the deadline, a
  finished request is downloaded (a failed download tried again a few
  times: the result is paid for), a failed one fails the job;
- past the deadline the request stays remembered and the job fails with
  `ProviderTimeoutError`, which recovery later finishes;
- a finished request stays remembered until its job is saved
  (`stores.jobs.run_job` forgets it): the tool's tail (CapCut export,
  asset store) can still be cut off by a restart, and a re-run then
  downloads the same result again instead of paying (prod, 2026-10-22).
"""

import random
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.base import Provider, TaskState
from ofmhelpers.aigenproviders.errors import (
    ProviderError,
    ProviderGenerationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.spec import ModelSpec, OutputKind, Payload
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

OnResultUrls = Callable[[list[str]], None]

_DEFAULT_EXT: dict[OutputKind, str] = {"image": "png", "video": "mp4"}

# Each rate-limit wait is stretched by up to this much, so the runs one
# burst refused do not all come back in the same second.
_JITTER = 0.25


def _sleep(seconds: float) -> None:
    """Every retry wait of a submit or a download (tests replace it)."""
    time.sleep(seconds)


def _submit(provider: Provider, spec: ModelSpec, payload: Payload, key: str) -> str:
    """`provider.submit`, sent again only after a rate limit -- the provider
    created nothing then -- once per `rate_limit_waits_s` entry and never
    waiting more than `rate_limit_max_wait_s` in all. A timeout or a 5xx may
    have created a paid task: raised at once, never resent."""
    s = settings.aigen
    waits = iter(s.rate_limit_waits_s)
    left = s.rate_limit_max_wait_s
    while True:
        try:
            return provider.submit(spec, payload, idempotency_key=key)
        except ProviderRateLimitError as exc:
            backoff = next(waits, None)
            if backoff is None:
                raise
            wait_s = exc.retry_after_s
            if wait_s is None:
                spread = random.uniform(1, 1 + _JITTER)  # noqa: S311 -- spread, not crypto
                wait_s = backoff * spread
            if wait_s > left:
                raise
            left -= wait_s
            logger.warning(
                "%s rate-limited the submit of %s, sending it again in %.0f s",
                provider.label,
                key,
                wait_s,
            )
            _sleep(wait_s)


def _task_id(
    provider: Provider, spec: ModelSpec, payload: Payload, job_id: str, part: int
) -> str:
    task = tasks.recall(job_id, part)
    if task is not None and task.provider == provider.name and task.model == spec.key:
        logger.info("job %s resumes %s task %s", job_id, provider.name, task.task_id)
        return task.task_id
    key = job_id if part == 0 else f"{job_id}:{part}"
    task_id = _submit(provider, spec, payload, key)
    tasks.remember(
        tasks.ProviderTask(
            job_id=job_id,
            part=part,
            provider=provider.name,
            model=spec.key,
            task_id=task_id,
            api_key=provider.api_key,
        )
    )
    logger.info("job %s submitted %s task %s", job_id, spec.key, task_id)
    return task_id


def wait(provider: Provider, task_id: str, kind: OutputKind) -> TaskState:
    """Poll with backoff until the request ends or the deadline passes."""
    s = settings.aigen
    timeout_s = s.video_poll_timeout_s if kind == "video" else s.poll_timeout_s
    deadline = time.monotonic() + timeout_s
    interval = s.poll_interval_s
    while time.monotonic() < deadline:
        try:
            state = provider.status(task_id)
        except ProviderError as exc:
            if not exc.retryable:
                raise
            logger.warning("status of %s failed, retrying", task_id, exc_info=True)
        else:
            if state.state != "pending":
                return state
        time.sleep(interval)
        interval = min(interval * 1.5, s.poll_max_interval_s)
    msg = (
        f"{provider.label} is still generating (task {task_id}, waited "
        f"{timeout_s}s). No action needed -- recovery finishes this job once "
        f"{provider.label} does."
    )
    raise ProviderTimeoutError(msg)


def _result_path(
    task_id: str, provider: str, url: str, index: int, count: int, kind: OutputKind
) -> Path:
    ext = Path(urlparse(url).path).suffix.lstrip(".") or _DEFAULT_EXT[kind]
    suffix = f"_{index}" if count > 1 else ""
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
    out_dir = Path(settings.aigen.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{provider}_{safe_id}{suffix}.{ext}"


def _download(provider: Provider, url: str, dest: Path) -> Path:
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


def generate(
    provider: Provider,
    spec: ModelSpec,
    payload: Payload,
    job_id: str,
    on_result_urls: OnResultUrls | None = None,
    part: int = 0,
) -> list[Path]:
    """Run `payload` on `spec` for `job_id` and save every result locally.
    `on_result_urls` gets the hosted results the moment they exist, before
    the download -- the page shows them while it runs. `part`: which of
    the job's requests this is, when it makes several (`tasks`)."""
    task_id = _task_id(provider, spec, payload, job_id, part)
    state = wait(provider, task_id, spec.kind)
    if state.state == "failed" or not state.urls:
        tasks.forget(job_id)
        msg = f"{provider.label} failed this generation: {state.error or 'no result'}"
        raise ProviderGenerationError(msg)
    if on_result_urls is not None:
        on_result_urls(state.urls)
    count = len(state.urls)
    return [
        _download(
            provider,
            url,
            _result_path(task_id, provider.name, url, i, count, spec.kind),
        )
        for i, url in enumerate(state.urls)
    ]
