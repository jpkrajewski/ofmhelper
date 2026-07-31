"""
Nightly (and on-demand) sweep: scrape followers + last-3-posts stats for
every Instagram account in the models roster and persist the latest result.
Enqueued by the "Refresh stats" button in routers/admin/models.py, and scheduled
to recur at UTC midnight via `ensure_scheduled` -- see the "recurring" note
below for why that's a plain RQ `enqueue_at`, not a background thread.

One account's scrape failing must never stop the sweep over the rest, so
each account is isolated in its own try/except (see instagram_public.py's
own docstring for why scraping itself already isolates per-post failures).

Recurring via self-rescheduling: plain RQ (unlike the separate rq-scheduler
package) has no cron primitive, only `enqueue_at` for a single future run.
So `collect_all_instagram_stats` schedules its OWN next run at the end,
via RQ's built-in scheduler (a separate OS process worker.py's `rq
worker-pool` already starts -- see rq.scheduler). Both `ensure_scheduled`
(worker boot) and the end of a sweep check the ScheduledJobRegistry itself
for a pending run of this function before adding one, rather than reusing
a fixed job_id -- a fixed ID would collide with the very job that's
mid-run when it's the scheduled one rescheduling itself.

Deliberately NOT a `threading.Thread` in worker.py (an earlier version of
this did that): `rq worker-pool` forks a fresh OS process per job
(Worker.execute_job -> fork_work_horse), and fork()ing a process that has
an extra live thread is unsafe -- a lock that thread held at the exact
moment of fork can stay locked forever in the child. Confirmed live: every
job hung indefinitely (STARTED forever, no exception) only once that
thread existed in the parent worker process.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.scraping.instagram_public import fetch_profile_stats
from ofmhelpers.utils.profile_loader import InstagramNormalizer
from ofmhelpers.web.stores import instagram_stats
from ofmhelpers.web.stores import models as models_store

logger = get_logger(__name__)

_normalizer = InstagramNormalizer()


def _next_sweep_time_utc() -> datetime:
    """Tomorrow at the configured sweep hour -- always in the future, so a
    sweep that finishes at 00:05 schedules the next one for tomorrow rather
    than re-firing minutes later."""
    hour = settings.instagram_stats.sweep_hour_utc
    now = datetime.now(UTC)
    return (now + timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )


def _already_scheduled() -> bool:
    from rq.registry import ScheduledJobRegistry

    from ofmhelpers.web.queue import get_queue

    queue = get_queue()
    registry = ScheduledJobRegistry(queue=queue)
    for job_id in registry.get_job_ids():
        job = queue.fetch_job(job_id)
        if job is not None and (job.func_name or "").endswith(
            "collect_all_instagram_stats"
        ):
            return True
    return False


def active_sweep_id() -> str | None:
    """Job id of a sweep already queued or running, if any.

    Two concurrent sweeps would scrape the same accounts twice over and make
    Instagram's soft block far more likely, so the refresh button reuses a
    live sweep instead of starting a second one."""
    from rq.registry import StartedJobRegistry

    from ofmhelpers.web.queue import get_queue

    queue = get_queue()
    ids = list(queue.get_job_ids()) + list(
        StartedJobRegistry(queue=queue).get_job_ids()
    )
    for job_id in ids:
        job = queue.fetch_job(job_id)
        if job is not None and (job.func_name or "").endswith(
            "collect_all_instagram_stats"
        ):
            return job_id
    return None


def ensure_scheduled() -> None:
    """Seeds the next midnight-UTC sweep if one isn't already pending in
    RQ's ScheduledJobRegistry. Call at worker boot (see worker.py) and
    again at the end of every sweep, so the chain keeps extending itself
    one day at a time."""
    from ofmhelpers.web.queue import get_queue

    if _already_scheduled():
        return
    when = _next_sweep_time_utc()
    get_queue().enqueue_at(when, collect_all_instagram_stats)
    logger.info("instagram stats sweep scheduled for %s", when)


def collect_all_instagram_stats() -> None:
    accounts = [
        (account["id"], account["url"])
        for model in models_store.list_models()
        for account in model["instagram_accounts"]
    ]
    logger.info("instagram stats sweep starting: %d account(s)", len(accounts))

    for account_id, url in accounts:
        try:
            username = _normalizer.normalize(url)
            stats = fetch_profile_stats(username)
            instagram_stats.save_stats(
                account_id,
                followers=stats.followers,
                posts=[asdict(p) for p in stats.posts],
                error=stats.error,
            )
        except Exception as exc:
            logger.warning(
                "instagram stats sweep failed account_id=%s url=%s: %s",
                account_id,
                url,
                exc,
                exc_info=True,
            )
            instagram_stats.save_stats(
                account_id, followers=None, posts=[], error=str(exc)
            )

    logger.info("instagram stats sweep done")

    # Whether this run was the scheduled midnight sweep or a manual
    # "Refresh stats" click, make sure tomorrow's run is queued.
    try:
        ensure_scheduled()
    except Exception:
        logger.exception("instagram stats sweep: failed to reschedule next run")
