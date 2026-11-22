from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from ofmhelpers.cache import QueueName, enqueue_to
from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.db.jobs.store import merge_params, reopen_job, reopen_settled
from ofmhelpers.features.jobs.runner import run_job

RESTARTS_MAX = 2
COUNT = "restarts"


class RunArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class Restart(BaseModel):
    model_config = ConfigDict(frozen=True)

    run: Callable[..., Any]
    kwargs: Callable[[Job], RunArgs]
    queue: QueueName = QueueName.DEFAULT
    job_timeout: Callable[[dict[str, Any]], int] | None = None

    def again(self, job: Job) -> bool:
        restarts = int(job.params.get(COUNT, 0))
        if restarts >= RESTARTS_MAX:
            return False
        kwargs = self.kwargs(job).model_dump()
        merge_params(job.id, {COUNT: restarts + 1})
        reopen_job(job.id)
        enqueue_to(
            self.queue,
            run_job,
            (job.id, self.run, kwargs),
            job_timeout=self.job_timeout(job.params) if self.job_timeout else None,
        )
        return True

    def replay(self, job: Job) -> bool:
        kwargs = self.kwargs(job).model_dump()
        if not reopen_settled(job.id):
            return False
        enqueue_to(
            self.queue,
            run_job,
            (job.id, self.run, kwargs),
            job_timeout=self.job_timeout(job.params) if self.job_timeout else None,
        )
        return True
