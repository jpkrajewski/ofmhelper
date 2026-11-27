# Copyright (C) 2026 jpkrajewski

from ofmhelpers.db.jobs.store import keep_execution
from ofmhelpers.media.repurpose.pipelines.keeper import ExecutionKeeper
lazy from ofmhelpers.media.repurpose.pipelines.models import Execution


class JobExecutions(ExecutionKeeper):
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def keep(self, execution: Execution) -> None:
        keep_execution(self.job_id, execution.model_dump(mode="json"))
