"""A job cut off by a restart, queued again from its stored params.

A deploy gives the workers 120 s (`stop_grace_period`) and a job may run for
`rq_job_timeout_s` (45 min), so some are killed mid-run. Recovery
(`recovery.py`) starts over the ones registered here (`routers/registry.py`
`RESTARTS`) instead of failing them "Interrupted by a restart".

Only work that is free and ends the same when run twice belongs there: local
ffmpeg, a download, a file combined from uploads. Never a task that pays a
provider: that one continues from its stored provider task id (`RESUMABLE`)
or not at all.
"""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from ofmhelpers.cache import QueueName, enqueue_to
from ofmhelpers.db.stores.jobs import merge_params, reopen_job
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.schemas import Job

# Cut off this often, a job is failed: something kills it (memory, a file
# ffmpeg cannot get through), and it would start over for ever.
RESTARTS_MAX = 2
# Counted on the job's own params, like a /trials Generate's.
COUNT = "restarts"


class Restart(BaseModel):
    """How one task's job starts over."""

    model_config = ConfigDict(frozen=True)

    # The worker function its jobs run: the one its route queues.
    run: Callable[..., Any]
    # What `run` is called with, from the stored job (its params, its actor).
    kwargs: Callable[[Job], dict[str, Any]]
    queue: QueueName = QueueName.DEFAULT
    # The RQ time limit of a job with these params (None: the queue's).
    job_timeout: Callable[[dict[str, Any]], int] | None = None

    def again(self, job: Job) -> bool:
        """Queues `job` again; False (it is failed as interrupted) once it
        was cut off `RESTARTS_MAX` times."""
        restarts = int(job.params.get(COUNT, 0))
        if restarts >= RESTARTS_MAX:
            return False
        # Before anything is written: params this cannot read raise here.
        kwargs = self.kwargs(job)
        # Counted first: a restart that dies on the way still counts.
        merge_params(job.id, {COUNT: restarts + 1})
        reopen_job(job.id)
        enqueue_to(
            self.queue,
            run_job,
            job.id,
            self.run,
            kwargs,
            job_timeout=self.job_timeout(job.params) if self.job_timeout else None,
        )
        return True
