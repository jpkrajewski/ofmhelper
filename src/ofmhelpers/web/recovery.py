"""
ofmhelpers/web/recovery.py

Background recovery sweeper: every few minutes, sweep kie.ai's task log for
generations that were created but never downloaded (the in-request poll
timed out, the server restarted mid-generation, ...) and pull them down to
the server automatically. This is what makes a poll timeout a non-event --
nobody has to log into kie.ai and check manually.

Runs with the API keys configured in the environment (admin first, then VA);
a task created with any other key (one pasted into a form) is checked with
that key, which KieAIClient keeps for it. KieAIClient's resolved-log
bookkeeping guarantees each terminal task is only ever handled once, and its
age cutoff guarantees nothing is re-checked forever.

A task logged with its job finishes that job: a job that gave up on it is
re-run (it picks the same task back up, so its tool downloads and
post-processes the result as usual), or failed with kie.ai's own reason.
"""

import asyncio

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.routers.registry import RESUMABLE
from ofmhelpers.web.routers.task_helpers import assets_root_for_role
from ofmhelpers.web.schemas import JobStatus
from ofmhelpers.web.stores.jobs import fail_job, get_job, reopen_job, run_job

logger = get_logger(__name__)

SWEEP_INTERVAL_S = settings.web.recovery_sweep_interval_s


def _configured_keys() -> list[str]:
    s = settings.web
    keys = []
    for raw_key in (s.kie_ai_api_key_admin, s.kie_ai_api_key_va):
        key = (raw_key or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def finish_job(record: dict) -> None:
    """Settle the job a recovered task belongs to. Only a job that already
    failed (it gave up polling) is touched: a running or done one is still
    handling its task, or has."""
    job = get_job(record["jobId"])
    if job is None or job.status != JobStatus.failed:
        return
    if record["outcome"] == "fail":
        fail_job(job.id, f"kie.ai failed this generation: {record['error']}")
        return
    run = RESUMABLE.get(job.task)
    if run is None:
        return
    reopen_job(job.id)
    enqueue(
        run_job,
        job.id,
        run,
        {
            **job.params,
            "job_id": job.id,
            "api_key": record["apiKey"],
            "assets_root": str(assets_root_for_role(job.actor)),
        },
    )
    logger.info("recovery re-running job %s for task %s", job.id, record["taskId"])


def run_recovery_once() -> list[dict]:
    """One sweep across all configured keys. Blocking (requests) -- call via
    asyncio.to_thread from the event loop."""
    recovered = []
    for key in _configured_keys():
        try:
            records = KieAIClient.from_env(api_key=key).resume_pending()
        except Exception:
            # A broken sweep must never take the loop down -- log and move on.
            logger.exception("recovery sweep failed for one key")
            continue
        for record in records:
            if record.get("jobId"):
                try:
                    finish_job(record)
                except Exception:
                    logger.exception(
                        "recovery could not finish job %s", record["jobId"]
                    )
        recovered += records
    return recovered


async def recovery_loop() -> None:
    while True:
        await asyncio.to_thread(run_recovery_once)
        await asyncio.sleep(SWEEP_INTERVAL_S)
