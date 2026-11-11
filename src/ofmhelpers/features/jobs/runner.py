"""Running a job: `start_job` writes its row and queues `run_job`, the wrapper
every background job runs in. It records `done` + result or `failed` + error
on the row (`db/stores/jobs.py`), keeps what the run cost, forgets or keeps
its provider requests for recovery, traces the run and queues the result's
gallery previews.

Jobs queued before this lived in the store name
`ofmhelpers.db.stores.jobs.run_job`: that name hands them here
(`register_runner`, last line)."""

from typing import Any
lazy from collections.abc import Callable

import logfire
from redis.exceptions import RedisError
from rq.timeouts import JobTimeoutException

from ofmhelpers.cache import QueueName, enqueue_to
from ofmhelpers.db.stores import jobs as store
from ofmhelpers.features.previews.render import warm_previews
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.errors import ProviderError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.persistence import GenerationRecord, JobMeta

logger = get_logger(__name__)


def resumable(exc: BaseException) -> bool:
    """Whether a job failing with `exc` keeps its provider request(s) for
    recovery to finish: a provider error (timed out, download failed) or RQ
    ending the job at its time limit -- paid pieces of a long clone
    included -- or an error that says so itself (`keep_request`: a /clone
    export that failed every try, re-run a bounded number of times).
    Anything else failed after the result was in (asset store): recovery
    re-running it would fail the same way every sweep."""
    return isinstance(exc, (ProviderError, JobTimeoutException)) or bool(
        getattr(exc, "keep_request", False)
    )


def run_job(job_id: str, fn: Callable[..., Any], kwargs: dict) -> None:
    """Call this via queue.enqueue(run_job, job_id, fn, kwargs) (or, until an
    endpoint is migrated, BackgroundTasks.add_task). Writes the terminal
    status transition straight to Postgres so the API process sees it even
    when this runs in a separate worker. One trace per run (telemetry.py):
    the job, who started it, and every call it makes inside.

    A job whose row is gone (the history cap, a heal) is not run: nobody
    could see or review what it made, yet it would pay the provider."""
    header = store.task_of(job_id)
    if header is None:
        logger.warning("job %s no longer exists: not run", job_id)
        tasks.forget(job_id)
        return
    attributes: dict[str, Any] = {
        "job_id": job_id,
        "task": header.task,
        "actor": header.actor,
        "function": fn.__qualname__,
        "enduser.id": header.actor,
    }
    with logfire.span("job {task} {job_id}", **attributes) as span:
        try:
            result = fn(**kwargs)
            store.finish_job(job_id, result)
            _record_outcome(span, job_id, result)
            # Saved: its provider request (if any) needs no resuming.
            _keep_cost(job_id)
            tasks.forget(job_id)
        except Exception as exc:
            # The status pages show this directly to whoever is running the
            # job -- a raw traceback isn't useful to them, just the
            # exception's own message (e.g. "Wrong API Key"). Full detail
            # goes to the logs and the trace for whoever is debugging it.
            store.record_failure(job_id, str(exc) or exc.__class__.__name__)
            if not resumable(exc):
                # Paid for and then failed (its export, its upload): still a cost.
                _keep_cost(job_id)
                tasks.forget(job_id)
            span.record_exception(exc)
            span.set_level("error")
            logger.exception("job %s failed", job_id)
            return
    _queue_previews(job_id)


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
    """A new job and its run on `queue` (default: the default queue), at the
    front of its line with `at_front` (`JobQueue.put`).
    `kwargs`: what `run` gets besides `job_id`; a key goes there, never in
    `params`, which the job's page shows. A queue failure is raised as it
    came and the row stays `running`: recovery settles it as cut off."""
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
    """A job that ran to its end can still have failed: a download of three
    links stores which ones did, as results, and is `done`. Only the span and
    the log say so, or a trace of a failed download reads as a success."""
    items = result if isinstance(result, list) else []
    failed = [item for item in items if getattr(item, "success", True) is False]
    if not failed:
        return
    span.set_attributes({"results": len(items), "results_failed": len(failed)})
    span.set_level("error" if len(failed) == len(items) else "warning")
    logger.warning(
        "job %s: %d of %d results failed: %s",
        job_id,
        len(failed),
        len(items),
        [getattr(item, "error", None) for item in failed],
    )


def _queue_previews(job_id: str) -> None:
    """A finished job's gallery previews, made now on the previews worker so
    its first viewer waits for none (see features/previews/render.py). Redis down: the
    preview routes make them on request instead."""
    job = store.get_job(job_id)
    if job is None:
        return
    try:
        enqueue_to(QueueName.PREVIEWS, warm_previews, job.id, job.local_paths())
    except RedisError:
        logger.warning("could not queue previews of job %s", job_id, exc_info=True)


def _keep_cost(job_id: str) -> None:
    """The job's provider requests into its row (`jobs.meta`) before they are
    forgotten: Logfire keeps their cost 30 days, the job keeps it for good.
    Never the key they ran on. Best effort: the job is already saved."""
    try:
        made = [
            GenerationRecord.model_validate(task.model_dump(exclude={"api_key"}))
            for task in tasks.of_job(job_id)
        ]
        if made:
            store.set_job_meta(job_id, JobMeta.of(made))
    except Exception:
        logger.warning("could not keep the cost of job %s", job_id, exc_info=True)


store.register_runner(run_job)
