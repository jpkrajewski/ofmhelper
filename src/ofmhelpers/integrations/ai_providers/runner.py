# Copyright (C) 2026 jpkrajewski

import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import logfire

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers import self_heal, tasks
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderError,
    ProviderGenerationError,
    ProviderNoCreditsError,
    ProviderTimeoutError,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.ai_providers.base import Provider, TaskState
lazy from ofmhelpers.integrations.ai_providers.spec import (
    ModelSpec,
    OutputKind,
    Payload,
)

logger = get_logger(__name__)

OnResultUrls = Callable[[list[str]], None]

_DEFAULT_EXT: dict[OutputKind, str] = {"image": "png", "video": "mp4"}


def _task_id(
    provider: Provider,
    spec: ModelSpec,
    payload: Payload | Callable[[], Payload],
    job_id: str,
    part: int,
) -> str:
    task = tasks.recall(job_id, part)
    if task is not None and task.provider == provider.name and task.model == spec.key:
        logger.info("job %s resumes %s task %s", job_id, provider.name, task.task_id)
        return task.task_id
    task_id = self_heal.new_task(provider, spec, payload, job_id, part)
    tasks.remember(
        tasks.ProviderTask(
            job_id=job_id,
            part=part,
            provider=provider.name,
            model=spec.key,
            task_id=task_id,
            api_key=provider.api_key,
        ),
    )
    logger.info("job %s submitted %s task %s", job_id, spec.key, task_id)
    return task_id


def wait(provider: Provider, task_id: str, kind: OutputKind) -> TaskState:
    s = settings.aigen
    timeout_s = s.video_poll_timeout_s if kind == "video" else s.poll_timeout_s
    deadline = time.monotonic() + timeout_s
    floor = provider.min_poll_interval_s()
    interval = max(s.poll_interval_s, floor)
    while time.monotonic() < deadline:
        try:
            state = provider.status(task_id)
        except ProviderError as exc:
            if not exc.retryable:
                raise
            logger.warning("status of %s failed, retrying", task_id, exc_info=True)
        else:
            if state.state == "failed" or state.urls:
                return state
            if state.state == "done":
                logger.warning("task %s finished without a result yet", task_id)
        time.sleep(interval)
        interval = max(min(interval * 1.5, s.poll_max_interval_s), floor)
    msg = (
        f"{provider.label} is still generating (task {task_id}, waited "
        f"{timeout_s}s). No action needed -- recovery finishes this job once "
        f"{provider.label} does."
    )
    raise ProviderTimeoutError(msg)


def _result_path(  # noqa: PLR0913, PLR0917
    task_id: str,
    provider: str,
    url: str,
    index: int,
    count: int,
    kind: OutputKind,
) -> Path:
    ext = Path(urlparse(url).path).suffix.lstrip(".") or _DEFAULT_EXT[kind]
    suffix = f"_{index}" if count > 1 else ""
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)
    out_dir = Path(settings.aigen.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{provider}_{safe_id}{suffix}.{ext}"


def generate(  # noqa: PLR0913, PLR0917
    provider: Provider,
    spec: ModelSpec,
    payload: Payload | Callable[[], Payload],
    job_id: str,
    on_result_urls: OnResultUrls | None = None,
    part: int = 0,
) -> list[Path]:
    with logfire.span(
        "generation {provider} {model}",
        provider=provider.name,
        model=spec.key,
        job_id=job_id,
        part=part,
    ) as span:
        waits = settings.aigen.unfetched_retry_waits_s
        sent = 0
        while True:
            task_id = _task_id(provider, spec, payload, job_id, part)
            sent += 1
            span.set_attribute("task_id", task_id)
            state = wait(provider, task_id, spec.kind)
            if not state.unfetched or sent > len(waits):
                break
            lighter = sent == len(waits) and provider.send_lighter()
            logger.warning(
                "job %s: %s task %s could not fetch a reference (%s); "
                "sent again in %s s (try %s%s)",
                job_id,
                provider.name,
                task_id,
                state.error,
                waits[sent - 1],
                sent + 1,
                ", lighter references" if lighter else "",
            )
            tasks.forget_part(job_id, part)
            time.sleep(waits[sent - 1])
        span.set_attributes(
            {"state": state.state, "results": len(state.urls), "sent": sent},
        )
        if state.cost_usd is not None:
            span.set_attribute("cost_usd", state.cost_usd)
        tasks.settle(job_id, part, state.state, state.cost_usd)
        if state.state == "failed":
            tasks.forget(job_id)
            msg = (
                f"{provider.label} failed this generation: {state.error or 'no result'}"
            )
            if state.unfetched:
                msg = f"{state.error}. Tried {sent} times, nothing was charged"
            if state.no_credits:
                raise ProviderNoCreditsError(msg, provider=provider.label)
            raise ProviderGenerationError(msg)
        if on_result_urls is not None:
            on_result_urls(state.urls)
        count = len(state.urls)
        return [
            self_heal.download(
                provider,
                url,
                _result_path(task_id, provider.name, url, i, count, spec.kind),
            )
            for i, url in enumerate(state.urls)
        ]
