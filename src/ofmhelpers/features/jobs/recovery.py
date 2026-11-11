"""
ofmhelpers/features/jobs/recovery.py (`registry` below: features/jobs/registry.py)

Background recovery sweeper: every few minutes, settle the jobs whose
provider request outlived their poll (it timed out, the worker restarted
mid-generation, ...). This is what makes a poll timeout a non-event --
nobody has to log into a provider and check manually.

It works off `integrations/ai_providers.tasks`: every accepted request is remembered
with its job, provider and key until its result is saved. For each one whose
job already **failed** (gave up waiting), one status check decides:

- done   -> the job is re-run with its stored params (`registry.RESUMABLE`);
            the re-run resumes the same request instead of paying again, so
            the tool's own tail (CapCut, asset store) runs as usual;
- failed -> the job's error becomes the provider's reason;
- still running -> left for the next sweep.

A job is left alone while a worker holds it or it waits in a queue
(`cache.queue.active_job_ids`), whatever its stored status; a re-run goes
on its task's own queue (`registry.QUEUES`). A running job neither holds
(a deploy killed its worker mid-job; prod, 2026-10-22: a clone's CapCut
export) is cut off, judged against a view of the queues read after the
slow part of the sweep: with a remembered request
it is settled like a failed one (resumed, never paid twice); one whose task
can start over (`RESTARTS`: a /trials Generate, local ffmpeg and download
work; never a task that pays a provider) is queued again; any other
fails with "Interrupted by a restart", so no card spins forever. A done or
deleted job is forgotten, and nothing older than `resume_max_age_s` is
checked again. Provider-agnostic: a new provider is recovered without a
line here.
"""

import time
from collections.abc import Callable

from ofmhelpers.cache import QueueName, enqueue_to
from ofmhelpers.cache.queue import active_job_ids
from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import fail_job, get_job, reopen_job, running_jobs
from ofmhelpers.features.assets.store import assets_root_for_actor
from ofmhelpers.features.clone_links.review import resume_reviews
from ofmhelpers.features.jobs.registry import JOB_TIMEOUTS, QUEUES, RESUMABLE
from ofmhelpers.features.jobs.registry import RESTARTS as LOCAL_RESTARTS
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.trials import folder as trials_folder
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.base import get_provider
from ofmhelpers.integrations.ai_providers.errors import ProviderError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import Job, JobStatus
from ofmhelpers.utils.registry import Registry

logger = get_logger(__name__)

INTERRUPTED = "Interrupted by a restart: run it again."
# Cut off with nothing to resume, these start over (True: queued again): a
# /trials Generate, and the tasks whose work is local and free to repeat
# (`registry.RESTARTS`, features/jobs/restart.py). Never one that pays a
# provider.
RESTARTS: frozendict[str, Callable[[Job], bool]] = frozendict(
    {
        trials_folder.TASK: trials_folder.restart,
        **{task: restart.again for task, restart in LOCAL_RESTARTS.items()},
    }
)


def _cut_off(job: Job, active: set[str] | None) -> bool:
    """Running, yet no worker holds it and the queue does not either."""
    return (
        active is not None
        and job.status == JobStatus.running
        and job.id not in active
        and time.time() - job.created_at >= settings.web.recovery_orphan_grace_s
    )


def settle_job(parts: list[tasks.ProviderTask], active: set[str] | None) -> str:
    """Settle a job by every request it remembers (one per piece of a long
    /clone video): any failed fails it, any still generating waits, all
    done re-runs it (each piece resumes, none is paid twice)."""
    job_id = parts[0].job_id
    job = get_job(job_id)
    if job is None or job.status == JobStatus.done:
        tasks.forget(job_id)
        return "forgotten"
    if active is not None and job.id in active:
        # A worker holds it or the queue has it, whatever its stored status
        # (one failed by mistake still runs): a re-run now would run it
        # twice at once, and pay again once the first forgot its request.
        return "running"
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
    if tasks.waiting(job_id):
        # A piece whose submit answer was lost (`tasks.remember_sent`): a
        # re-run now would send it again and maybe pay twice. Its callback
        # names it, and the next sweep re-runs the job then.
        return "waiting"
    run = RESUMABLE.get(job.task)
    if run is None:
        tasks.forget(job_id)
        return "not resumable"
    reopen_job(job.id)
    timeout = JOB_TIMEOUTS.get(job.task)
    enqueue_to(
        QUEUES.get(job.task, QueueName.DEFAULT),
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


def _restarted(job: Job) -> bool:
    restart = RESTARTS.get(job.task)
    if restart is None:
        return False
    try:
        return restart(job)
    except Exception:
        logger.exception("recovery could not start job %s again", job.id)
        return False


def fail_cut_off(active: set[str] | None, remembered: set[str]) -> Registry[str]:
    """Every cut-off running job with no request to resume started over
    (`RESTARTS`) or failed."""
    outcomes: dict[str, str] = {}
    for job in running_jobs():
        if job.id in remembered or not _cut_off(job, active):
            continue
        if _restarted(job):
            logger.warning(
                "recovery: job %s was cut off by a restart: started again", job.id
            )
            outcomes[job.id] = "restarted"
            continue
        fail_job(job.id, INTERRUPTED)
        logger.warning("recovery: job %s was cut off by a restart", job.id)
        outcomes[job.id] = "interrupted"
    return Registry[str](entries=outcomes)


def run_recovery_once() -> Registry[str]:
    """One sweep: job id -> outcome. Blocking -- features/jobs/sweeps.py runs it in a
    thread every `recovery_sweep_interval_s`. One bad task never stops the
    sweep."""
    outcomes: dict[str, str] = {}
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
        # A fresh view, not the one taken before the settle loop: a slow
        # provider can hold that loop past the grace, and a Generate queued
        # meanwhile would be failed as cut off while its runs wait in RQ.
        # Read before fail_cut_off reads the running rows, so a row made in
        # between is younger than the grace.
        cut_off = fail_cut_off(active_job_ids(), {t.job_id for t in remembered})
        outcomes |= cut_off.entries
    except Exception:
        logger.exception("recovery could not check for cut-off jobs")
    try:
        outcomes |= resume_reviews().entries
    except Exception:
        logger.exception("recovery could not resume /clone-links reviews")
    return Registry[str](entries=outcomes)
