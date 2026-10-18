"""One generation on any provider: submit (or resume), wait, save the result.

The only place the request lifecycle is written. A provider adapter answers
single calls; this decides what to do with each answer:

- a job that already has an accepted request (`tasks.recall`) resumes it
  instead of submitting again -- a re-run never pays twice;
- a status check that fails transiently is retried until the deadline, a
  finished request is downloaded, a failed one fails the job;
- past the deadline the request stays remembered and the job fails with
  `ProviderTimeoutError`, which recovery later finishes.
"""

import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.base import Provider, TaskState
from ofmhelpers.aigenproviders.errors import (
    ProviderError,
    ProviderGenerationError,
    ProviderTimeoutError,
)
from ofmhelpers.aigenproviders.spec import ModelSpec, OutputKind, Payload
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

OnResultUrls = Callable[[list[str]], None]

_DEFAULT_EXT: dict[OutputKind, str] = {"image": "png", "video": "mp4"}


def _task_id(provider: Provider, spec: ModelSpec, payload: Payload, job_id: str) -> str:
    task = tasks.recall(job_id)
    if task is not None and task.provider == provider.name and task.model == spec.key:
        logger.info("job %s resumes %s task %s", job_id, provider.name, task.task_id)
        return task.task_id
    task_id = provider.submit(spec, payload, idempotency_key=job_id)
    tasks.remember(
        tasks.ProviderTask(
            job_id=job_id,
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


def generate(
    provider: Provider,
    spec: ModelSpec,
    payload: Payload,
    job_id: str,
    on_result_urls: OnResultUrls | None = None,
) -> list[Path]:
    """Run `payload` on `spec` for `job_id` and save every result locally.
    `on_result_urls` gets the hosted results the moment they exist, before
    the download -- the page shows them while it runs."""
    task_id = _task_id(provider, spec, payload, job_id)
    state = wait(provider, task_id, spec.kind)
    if state.state == "failed" or not state.urls:
        tasks.forget(job_id)
        msg = f"{provider.label} failed this generation: {state.error or 'no result'}"
        raise ProviderGenerationError(msg)
    if on_result_urls is not None:
        on_result_urls(state.urls)
    count = len(state.urls)
    paths = [
        provider.download(
            url, _result_path(task_id, provider.name, url, i, count, spec.kind)
        )
        for i, url in enumerate(state.urls)
    ]
    tasks.forget(job_id)
    return paths
