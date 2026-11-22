import time
from types import MappingProxyType
lazy from collections.abc import Callable

from ofmhelpers.background.registry import JOB_TIMEOUTS, QUEUES, RESUMABLE
from ofmhelpers.background.registry import RESTARTS as LOCAL_RESTARTS
from ofmhelpers.cache import QueueName, enqueue_to
from ofmhelpers.cache.queue import active_job_ids
from ofmhelpers.config import settings
from ofmhelpers.db.jobs.schema import Job, JobStatus
from ofmhelpers.db.jobs.store import fail_job, get_job, reopen_job, running_jobs
from ofmhelpers.features.assets.store import assets_root_for_actor
from ofmhelpers.features.clone_links.review import resume_reviews
from ofmhelpers.features.jobs import notices
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.notifications import service as notifications
from ofmhelpers.features.trials import folder as trials_folder
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.base import get_provider
from ofmhelpers.integrations.ai_providers.errors import ProviderError
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

INTERRUPTED = "Interrupted by a restart: run it again."
RESTARTS: frozendict[str, Callable[[Job], bool]] = frozendict(
    {
        trials_folder.TASK: trials_folder.restart,
        **{task: restart.again for task, restart in LOCAL_RESTARTS.items()},
    }
)


def _ended(job: Job) -> None:
    if job.user_id is not None:
        notifications.changed(job.user_id)


def _cut_off(job: Job, active: set[str] | None) -> bool:
    return (
        active is not None
        and job.status == JobStatus.running
        and job.id not in active
        and time.time() - job.created_at >= settings.web.recovery_orphan_grace_s
    )


def settle_job(parts: list[tasks.ProviderTask], active: set[str] | None) -> str:
    job_id = parts[0].job_id
    job = get_job(job_id)
    if job is None or job.status == JobStatus.done:
        tasks.forget(job_id)
        return "forgotten"
    if active is not None and job.id in active:
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
            error = f"{provider.label} failed this generation: {state.error}"
            fail_job(job.id, error)
            _ended(job)
            if state.no_credits:
                notices.out_of_money(job.id, provider.label, error)
            return "failed"
        pending = pending or state.state == "pending"
    if pending:
        return "pending"
    if tasks.waiting(job_id):
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
        (
            job.id,
            run,
            {
                **job.params,
                "job_id": job.id,
                "api_key": parts[0].api_key,
                "assets_root": str(assets_root_for_actor(job.actor)),
            },
        ),
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


def fail_cut_off(
    active: set[str] | None, remembered: set[str]
) -> MappingProxyType[str, str]:
    outcomes: dict[str, str] = {}
    restarting = settings.web.restarting(settings.infra.production)
    for job in running_jobs():
        if job.id in remembered or not _cut_off(job, active):
            continue
        if job.task in RESTARTS and not restarting:
            logger.info("recovery: job %s left as copied (restarts off)", job.id)
            outcomes[job.id] = "left"
            continue
        if _restarted(job):
            logger.warning(
                "recovery: job %s was cut off by a restart: started again", job.id
            )
            outcomes[job.id] = "restarted"
            continue
        fail_job(job.id, INTERRUPTED)
        _ended(job)
        logger.error("recovery: job %s (%s) was cut off by a restart", job.id, job.task)
        outcomes[job.id] = "interrupted"
    return MappingProxyType(outcomes)


def run_recovery_once() -> MappingProxyType[str, str]:
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
            logger.warning("recovery: job %s check failed", job_id, exc_info=True)
        except Exception:
            logger.exception("recovery could not settle job %s", job_id)
    try:
        cut_off = fail_cut_off(active_job_ids(), {t.job_id for t in remembered})
        outcomes |= cut_off
    except Exception:
        logger.exception("recovery could not check for cut-off jobs")
    try:
        outcomes |= resume_reviews()
    except Exception:
        logger.exception("recovery could not resume /clone-links reviews")
    return MappingProxyType(outcomes)
