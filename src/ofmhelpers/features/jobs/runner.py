from typing import Any
lazy from collections.abc import Callable

import logfire
from opentelemetry import trace
from pydantic import BaseModel
from redis.exceptions import RedisError
from rq.timeouts import JobTimeoutException

from ofmhelpers.acting import Acting, Via, acting
from ofmhelpers.cache import QueueName, enqueue_to, running_job
from ofmhelpers.db.jobs import store
from ofmhelpers.db.jobs.schema import DownloadResult, GenerationRecord, JobMeta
from ofmhelpers.db.users import store as users
from ofmhelpers.features.account.keys import keep_used, key_tail
from ofmhelpers.features.clone_pricing.cost import cost_of
from ofmhelpers.features.jobs import notices
from ofmhelpers.features.notifications import service as notifications
from ofmhelpers.features.previews.render import warm_previews
from ofmhelpers.features.refusals import (
    AssetRefusedError,
    CloneRefusedError,
    ConflictError,
    EditRefusedError,
    ForbiddenError,
    NotFoundError,
    RefusedError,
    ResumableError,
    TooSoonError,
    TryAgainError,
)
from ofmhelpers.features.vas import notices as va_notices
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.errors import (
    InvalidInputError,
    ProviderAuthError,
    ProviderError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderRateLimitError,
)
from ofmhelpers.integrations.apify.client import ApifyCreditError
from ofmhelpers.integrations.llm.errors import LLMAuthError
from ofmhelpers.log import get_logger
from ofmhelpers.monitoring.sentry import user_scope
from ofmhelpers.redact import redact

logger = get_logger(__name__)

DEACTIVATED = "Not run: the account was deactivated"


class Requeued(BaseModel): ...


def resumable(exc: BaseException) -> bool:
    return isinstance(exc, (ProviderError, JobTimeoutException)) or (
        isinstance(exc, ResumableError) and exc.keep_request
    )


def refused(exc: BaseException) -> bool:
    return isinstance(
        exc,
        (
            ProviderAuthError,
            ProviderInputError,
            ProviderGenerationError,
            ProviderRateLimitError,
            InvalidInputError,
            ApifyCreditError,
            LLMAuthError,
            RefusedError,
            ForbiddenError,
            NotFoundError,
            ConflictError,
            TooSoonError,
            TryAgainError,
            AssetRefusedError,
            CloneRefusedError,
            EditRefusedError,
        ),
    )


def run_job(job_id: str, fn: Callable[..., Any], kwargs: dict) -> None:
    header = store.task_of(job_id)
    if header is None:
        logger.warning("job %s no longer exists: not run", job_id)
        tasks.forget(job_id)
        return
    if _actor_deactivated(header.actor):
        logger.warning(
            "job %s: actor %s was deactivated: not run", job_id, header.actor
        )
        store.record_failure(job_id, DEACTIVATED)
        tasks.forget(job_id)
        return
    attributes: dict[str, Any] = {
        "job_id": job_id,
        "task": header.task,
        "actor": header.actor,
        "function": fn.__qualname__,
        "enduser.id": header.actor,
    }
    with (
        logfire.span("job {task} {job_id}", **attributes) as span,
        acting(_job_acting(job_id, header.task, header.actor)),
        user_scope(header.actor),
    ):
        try:
            result = fn(**kwargs)
            if isinstance(result, Requeued):
                span.set_attributes({"requeued": True})
                _requeue(job_id, fn, kwargs)
                return
            store.finish_job(job_id, result)
            _record_outcome(span, job_id, result)
            _keep_cost(job_id)
            _keep_key(job_id, header.actor)
            tasks.forget(job_id)
            va_notices.after_job(job_id)
            _notifications_changed(header.actor)
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            store.record_failure(job_id, redact(error))
            if (provider := notices.money_provider(exc)) is not None:
                notices.out_of_money(job_id, provider, error)
            if not resumable(exc):
                _keep_cost(job_id)
                tasks.forget(job_id)
                va_notices.after_job(job_id)
                _notifications_changed(header.actor)
            span.record_exception(exc)
            span.set_level("error")
            if refused(exc):
                logger.warning("job %s refused: %s", job_id, error, exc_info=True)
            else:
                logger.exception("job %s failed", job_id)
            return
    _queue_previews(job_id)


def _notifications_changed(actor: str | None) -> None:
    if actor is not None and actor.isdecimal():
        notifications.changed(int(actor))


def _actor_deactivated(actor: str | None) -> bool:
    if actor is None or not actor.isdecimal():
        return False
    user = users.get_user(int(actor))
    return user is not None and not user.active


def start_job(
    task: str,
    params: dict,
    run: Callable[..., Any],
    kwargs: dict,
    *,
    actor: str | None,
    queue: QueueName | None = None,
    job_timeout: int | None = None,
    at_front: bool = False,
) -> str:
    job_id = store.create_job(task, params, actor=actor)
    enqueue_to(
        queue or QueueName.DEFAULT,
        run_job,
        job_id,
        run,
        {"job_id": job_id, **kwargs},
        job_timeout=job_timeout,
        at_front=at_front,
    )
    return job_id


def _record_outcome(span: logfire.LogfireSpan, job_id: str, result: Any) -> None:
    items = result if isinstance(result, list) else []
    failed = [
        item for item in items if isinstance(item, DownloadResult) and not item.success
    ]
    if not failed:
        return
    span.set_attributes({"results": len(items), "results_failed": len(failed)})
    span.set_level("error" if len(failed) == len(items) else "warning")
    logger.warning(
        "job %s: %d of %d results failed: %s",
        job_id,
        len(failed),
        len(items),
        [item.error for item in failed],
    )


def _job_acting(job_id: str, task: str, actor: str | None) -> Acting:
    context = trace.get_current_span().get_span_context()
    return Acting(
        via=Via.JOB,
        actor_id=int(actor) if actor and actor.isdigit() else None,
        ref=f"{task} {job_id}",
        trace_id=format(context.trace_id, "032x") if context.is_valid else "",
    )


def _requeue(job_id: str, fn: Callable[..., Any], kwargs: dict) -> None:
    running = running_job()
    queue = running.queue if running else QueueName.DEFAULT
    timeout = running.timeout if running else None
    enqueue_to(queue, run_job, job_id, fn, kwargs, job_timeout=timeout)
    logger.info("job %s back in line on %s", job_id, queue.value)


def _queue_previews(job_id: str) -> None:
    job = store.get_job(job_id)
    if job is None:
        return
    try:
        enqueue_to(QueueName.PREVIEWS, warm_previews, job.id, job.local_paths())
    except RedisError:
        logger.warning("could not queue previews of job %s", job_id, exc_info=True)


def _keep_cost(job_id: str) -> None:
    try:
        made = [
            GenerationRecord.model_validate(
                {
                    **task.model_dump(exclude={"api_key"}),
                    "key_tail": key_tail(task.api_key),
                }
            )
            for task in tasks.of_job(job_id)
        ]
        job = store.get_job(job_id)
        if made and job is not None:
            store.keep_cost(
                job_id, JobMeta(generations=made), cost_of(job.params, made)
            )
    except Exception:
        logger.warning("could not keep the cost of job %s", job_id, exc_info=True)


def _keep_key(job_id: str, actor: str | None) -> None:
    if actor is None or not actor.isdecimal():
        return
    try:
        keep_used(int(actor), tasks.of_job(job_id))
    except Exception:
        logger.warning("could not keep the key of job %s", job_id, exc_info=True)


store.register_runner(run_job)
