"""What a gallery renders for one job.

Every tool page with a gallery (/generate, /download-assets, /clone,
/character-sheet, /uniqify) shows its recent jobs as cards with the same core:
the job's id, status, error, params and result assets. Their templates and the
polling JS key off those fields, so they are one model, not a dict per router.

Data only: the result's asset cards come in already built (`job_assets` in
`routers/task_helpers`, which owns the file URLs).
"""

import json
from typing import Any, Self

from pydantic import BaseModel, computed_field

from ofmhelpers.web.schemas.drive import DriveUpload
from ofmhelpers.web.schemas.errors import error_reason
from ofmhelpers.web.schemas.jobs import AssetCard, FailedSource
from ofmhelpers.web.schemas.persistence import Job


class JobCard(BaseModel):
    job_id: str
    task: str
    status: str
    error: str | None
    created_at: float
    params: dict[str, Any]
    assets: list[AssetCard]
    failed_sources: list[FailedSource] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def params_json(self) -> str:
        return json.dumps(self.params)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_reason(self) -> str:
        """The failure as one short reason ("No credits", ...)."""
        return error_reason(self.error)

    @classmethod
    def from_job(
        cls,
        job: Job,
        assets: list[AssetCard],
        failed_sources: list[FailedSource],
        **extra: Any,
    ) -> Self:
        return cls(
            job_id=job.id,
            task=job.task,
            status=job.status,
            error=job.error,
            created_at=job.created_at,
            params=job.params,
            assets=assets,
            failed_sources=failed_sources,
            **extra,
        )


class TaskCard(JobCard):
    """A card in a gallery mixing several tools (/generate, /download-assets):
    which tool ran it, and the router prefix generation.js polls to resume it
    while it is still running."""

    task_label: str
    poll_prefix: str
    # What its result is while it runs (generation.js draws that kind when
    # the server does not say).
    result_kind: str = "image"


class CloneCard(TaskCard):
    """A /clone run's card: the shared card, plus where its video was
    uploaded in Drive (web/drive_uploads.py)."""

    drive_uploads: list[DriveUpload] = []
