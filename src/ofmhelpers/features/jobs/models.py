import json
from typing import Any, Self

from pydantic import BaseModel, computed_field

from ofmhelpers.db.clone_ai_checks.schema import CloneFromLink, ReviewReason
from ofmhelpers.db.drive_folders.schema import DriveUpload
from ofmhelpers.db.jobs.schema import JobPreview
from ofmhelpers.features.jobs.schema import (
    AssetCard,
    CloneSource,
    ErrorText,
    FailedSource,
    error_line,
    error_reason,
    error_text,
)
lazy from ofmhelpers.db.jobs.schema import Job


class JobStatusPayload(BaseModel):
    job_id: str
    task: str
    params: dict[str, Any]
    status: str
    error: str | None
    result: list[AssetCard]
    failed_sources: list[FailedSource]
    preview: JobPreview | None = None
    stage: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_reason(self) -> str:
        return error_reason(self.error)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_line(self) -> str:
        return error_line(self.error)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_text(self) -> ErrorText:
        return error_text(self.error)


class JobCard(BaseModel):
    job_id: str
    task: str
    status: str
    error: str | None
    created_at: float
    params: dict[str, Any]
    assets: list[AssetCard]
    failed_sources: list[FailedSource] = []
    hidden: bool = False
    made_by: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def params_json(self) -> str:
        return json.dumps(self.params)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_reason(self) -> str:
        return error_reason(self.error)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_line(self) -> str:
        return error_line(self.error)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def error_text(self) -> ErrorText:
        return error_text(self.error)

    @classmethod
    def from_job(
        cls,
        job: Job,
        assets: list[AssetCard],
        failed_sources: list[FailedSource],
        extra: dict[str, Any] | None = None,
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
            hidden=job.hidden_at is not None,
            made_by=job.made_by,
            **(extra or {}),
        )


class TaskCard(JobCard):
    task_label: str
    poll_prefix: str
    result_kind: str = "image"

    def per_file(self) -> list[Self]:
        if not self.assets:
            return [self]
        return [
            self.model_copy(
                update={
                    "assets": [asset],
                    "failed_sources": self.failed_sources if i == 0 else [],
                }
            )
            for i, asset in enumerate(self.assets)
        ]


class CloneCard(TaskCard):
    drive_uploads: list[DriveUpload] = []
    source: CloneSource | None = None


class CloneLinksCard(TaskCard):
    review: CloneFromLink | None = None
    saved_here: bool = True

    @classmethod
    def from_job(
        cls,
        job: Job,
        assets: list[AssetCard],
        failed_sources: list[FailedSource],
        extra: dict[str, Any] | None = None,
    ) -> Self:
        paths = job.local_paths()
        saved = bool(paths) and paths[0] is not None
        return super().from_job(
            job, assets, failed_sources, {"saved_here": saved, **(extra or {})}
        )

    @property
    def link(self) -> str:
        return str(self.params.get("link", ""))

    @property
    def missing_review(self) -> str | None:
        if self.status != "done" or self.review is not None:
            return None
        return ReviewReason.NOT_STARTED if self.saved_here else ReviewReason.NOT_SAVED

    @property
    def review_settled(self) -> bool:
        if self.status != "done":
            return self.status == "failed"
        return self.review is None or not self.review.running
