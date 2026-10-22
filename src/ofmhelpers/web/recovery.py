"""
ofmhelpers/web/recovery.py

Background recovery sweeper: every few minutes, settle the jobs whose
provider request outlived their poll (it timed out, the worker restarted
mid-generation, ...). This is what makes a poll timeout a non-event --
nobody has to log into a provider and check manually.

It works off `aigenproviders.tasks`: every accepted request is remembered
with its job, provider and key until its result is saved. For each one whose
job already **failed** (gave up waiting), one status check decides:

- done   -> the job is re-run with its stored params (`registry.RESUMABLE`);
            the re-run resumes the same request instead of paying again, so
            the tool's own tail (CapCut, asset store) runs as usual;
- failed -> the job's error becomes the provider's reason;
- still running -> left for the next sweep.

A job that is running is left alone, a done or deleted one is forgotten, and
nothing older than `resume_max_age_s` is checked again. Provider-agnostic:
a new provider is recovered without a line here.
"""

import asyncio

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.base import get_provider
from ofmhelpers.aigenproviders.errors import ProviderError
from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.routers.registry import RESUMABLE
from ofmhelpers.web.routers.task_helpers import assets_root_for_actor
from ofmhelpers.web.schemas import JobStatus
from ofmhelpers.web.stores.jobs import fail_job, get_job, reopen_job, run_job

logger = get_logger(__name__)

SWEEP_INTERVAL_S = settings.web.recovery_sweep_interval_s


def settle(task: tasks.ProviderTask) -> str:
    """Settle one remembered request; what happened, for the log and tests."""
    job = get_job(task.job_id)
    if job is None or job.status == JobStatus.done:
        tasks.forget(task.job_id)
        return "forgotten"
    if job.status != JobStatus.failed:
        return "running"
    if task.age_s > settings.aigen.resume_max_age_s:
        tasks.forget(task.job_id)
        return "expired"
    provider = get_provider(task.provider, task.api_key)
    state = provider.status(task.task_id)
    if state.state == "pending":
        return "pending"
    if state.state == "failed":
        tasks.forget(task.job_id)
        fail_job(job.id, f"{provider.label} failed this generation: {state.error}")
        return "failed"
    run = RESUMABLE.get(job.task)
    if run is None:
        tasks.forget(task.job_id)
        return "not resumable"
    reopen_job(job.id)
    enqueue(
        run_job,
        job.id,
        run,
        {
            **job.params,
            "job_id": job.id,
            "api_key": task.api_key,
            "assets_root": str(assets_root_for_actor(job.actor)),
        },
    )
    logger.info("recovery re-running job %s for task %s", job.id, task.task_id)
    return "rerun"


def run_recovery_once() -> dict[str, str]:
    """One sweep: job id -> outcome. Blocking -- call via asyncio.to_thread
    from the event loop. One bad task never stops the sweep."""
    outcomes = {}
    for task in tasks.remembered():
        try:
            outcomes[task.job_id] = settle(task)
        except ProviderError:
            # Network blip, provider down: the next sweep tries again.
            logger.warning("recovery: %s check failed", task.task_id, exc_info=True)
        except Exception:
            logger.exception("recovery could not settle job %s", task.job_id)
    return outcomes


async def recovery_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_recovery_once)
        except Exception:
            # A broken sweep (Redis down) must never take the loop down.
            logger.exception("recovery sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_S)
