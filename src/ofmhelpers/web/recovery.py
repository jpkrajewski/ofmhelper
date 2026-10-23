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

A running job is left alone while a worker holds it or it waits in the
queue. One that neither does (a deploy killed its worker mid-job; prod,
2026-10-22: a clone's CapCut export) is cut off: with a remembered request
it is settled like a failed one (resumed, never paid twice); without one it
fails with "Interrupted by a restart", so no card spins forever. A done or
deleted job is forgotten, and nothing older than `resume_max_age_s` is
checked again. Provider-agnostic: a new provider is recovered without a
line here.
"""

import asyncio
import time

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.base import get_provider
from ofmhelpers.aigenproviders.errors import ProviderError
from ofmhelpers.cache import enqueue
from ofmhelpers.cache.queue import active_job_ids
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.routers.registry import JOB_TIMEOUTS, RESUMABLE
from ofmhelpers.web.routers.task_helpers import assets_root_for_actor
from ofmhelpers.web.schemas import Job, JobStatus
from ofmhelpers.web.stores.jobs import (
    fail_job,
    get_job,
    reopen_job,
    run_job,
    running_jobs,
)

logger = get_logger(__name__)

SWEEP_INTERVAL_S = settings.web.recovery_sweep_interval_s

INTERRUPTED = "Interrupted by a restart: run it again."


def _cut_off(job: Job, active: set[str] | None) -> bool:
    """Running, yet no worker holds it and the queue does not either."""
    return (
        active is not None
        and job.status == JobStatus.running
        and job.id not in active
        and time.time() - job.created_at >= settings.web.recovery_orphan_grace_s
    )


def settle(task: tasks.ProviderTask, active: set[str] | None = None) -> str:
    """Settle one remembered request; what happened, for the log and tests.
    `active`: the job ids workers hold or the queue has (None: unknown)."""
    return settle_job([task], active)


def settle_job(parts: list[tasks.ProviderTask], active: set[str] | None) -> str:
    """Settle a job by every request it remembers (one per piece of a long
    /clone video): any failed fails it, any still generating waits, all
    done re-runs it (each piece resumes, none is paid twice)."""
    job_id = parts[0].job_id
    job = get_job(job_id)
    if job is None or job.status == JobStatus.done:
        tasks.forget(job_id)
        return "forgotten"
    if job.status != JobStatus.failed and not _cut_off(job, active):
        return "running"
    if max(t.age_s for t in parts) > settings.aigen.resume_max_age_s:
        tasks.forget(job_id)
        return "expired"
    pending = False
    for task in parts:
        provider = get_provider(task.provider, task.api_key)
        state = provider.status(task.task_id)
        if state.state == "failed":
            tasks.forget(job_id)
            fail_job(job.id, f"{provider.label} failed this generation: {state.error}")
            return "failed"
        pending = pending or state.state == "pending"
    if pending:
        return "pending"
    run = RESUMABLE.get(job.task)
    if run is None:
        tasks.forget(job_id)
        return "not resumable"
    reopen_job(job.id)
    timeout = JOB_TIMEOUTS.get(job.task)
    enqueue(
        run_job,
        job.id,
        run,
        {
            **job.params,
            "job_id": job.id,
            "api_key": parts[0].api_key,
            "assets_root": str(assets_root_for_actor(job.actor)),
        },
        job_timeout=timeout(job.params) if timeout else None,
    )
    logger.info("recovery re-running job %s (%d request(s))", job.id, len(parts))
    return "rerun"


def fail_cut_off(active: set[str] | None, remembered: set[str]) -> dict[str, str]:
    """Fail every cut-off running job with no request to resume."""
    outcomes = {}
    for job in running_jobs():
        if job.id not in remembered and _cut_off(job, active):
            fail_job(job.id, INTERRUPTED)
            logger.warning("recovery: job %s was cut off by a restart", job.id)
            outcomes[job.id] = "interrupted"
    return outcomes


def run_recovery_once() -> dict[str, str]:
    """One sweep: job id -> outcome. Blocking -- call via asyncio.to_thread
    from the event loop. One bad task never stops the sweep."""
    outcomes = {}
    active = active_job_ids()
    remembered = tasks.remembered()
    by_job: dict[str, list[tasks.ProviderTask]] = {}
    for task in remembered:
        by_job.setdefault(task.job_id, []).append(task)
    for job_id, parts in by_job.items():
        try:
            outcomes[job_id] = settle_job(sorted(parts, key=lambda t: t.part), active)
        except ProviderError:
            # Network blip, provider down: the next sweep tries again.
            logger.warning("recovery: job %s check failed", job_id, exc_info=True)
        except Exception:
            logger.exception("recovery could not settle job %s", job_id)
    try:
        outcomes |= fail_cut_off(active, {t.job_id for t in remembered})
    except Exception:
        logger.exception("recovery could not check for cut-off jobs")
    return outcomes


async def recovery_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_recovery_once)
        except Exception:
            # A broken sweep (Redis down) must never take the loop down.
            logger.exception("recovery sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_S)
