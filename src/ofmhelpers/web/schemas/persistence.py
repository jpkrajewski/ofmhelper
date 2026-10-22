"""
Pydantic v2 models for the job store that backs the web app
(web/stores/jobs.py). These are the typed contract at the persistence
boundary -- one source of truth for the shapes that used to live only as
ad-hoc dicts in JSON files.

Schemas only: no business logic, no DB, no I/O. The SQLAlchemy models
(web/db/models.py) and the repository layer (web/db/repository.py) bridge to
these via `model_validate(row)` thanks to `from_attributes=True`.

Field sets mirror exactly what the current code writes, so an existing
`uploads/*.json` record validates unchanged (that's what the step-5 backfill
relies on). File references are NOT a separate table -- they live inside a
job's `result` payload (a list of `ResultFile` dumps, a grouped list of
`DownloadResult` dumps, or a bare id string); `Job.result_files()` /
`Job.download_results()` read them back as models.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from ofmhelpers.downloaders.models import DownloadResult


class JobStatus(StrEnum):
    """The only three states web/stores/jobs.py ever assigns."""

    running = "running"
    done = "done"
    failed = "failed"


class ResultFile(BaseModel):
    """One output file in a job's stored `result` (the one-entry-per-file
    shape; grouped download results are `downloaders` models). `path` is None
    when only kie.ai's hosted copy exists; `remote_url` is set only by the
    kie.ai tools. Stored via `to_result()`, so a field never set stays out of
    the JSON exactly as before this was a model."""

    name: str
    path: str | None
    remote_url: str | None = None

    def to_result(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class JobPreview(BaseModel):
    """kie.ai's hosted result, published while the job is still downloading
    it locally -- the page plays it from kie.ai until the job is done.
    `kind` is "image"/"video"; older previews were stored without it."""

    remote_url: str
    kind: str | None = None


class Job(BaseModel):
    """A background-job record. `params` and `result` are free-form JSON
    (JSONB) because their shape varies per task type -- `result` is read back
    through `result_files()` / `download_results()`."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    params: dict[str, Any] = {}
    actor: str | None = None
    status: JobStatus = JobStatus.running
    result: list[Any] | dict[str, Any] | str | None = None
    error: str | None = None
    created_at: float
    finished_at: float | None = None
    preview: JobPreview | None = None

    @property
    def _entries(self) -> list[dict[str, Any]]:
        """The result as a list of entries, or [] for anything else (still
        running, failed, or a non-file result)."""
        result = self.result
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return result
        return []

    @property
    def is_grouped(self) -> bool:
        """A grouped download job: one `DownloadResult` per source URL,
        rather than one `ResultFile` per output file."""
        entries = self._entries
        return bool(entries) and "success" in entries[0]

    def result_files(self) -> list[ResultFile]:
        """The one-entry-per-file result (generation tools, clean, sheet,
        uniqify); [] for a grouped job or no result."""
        if self.is_grouped:
            return []
        return [ResultFile.model_validate(f) for f in self._entries]

    def local_paths(self) -> list[str | None]:
        """Every result file's local path, in the order `/files/{job}/{i}`
        numbers them: across every group for a grouped download job, one
        entry per file otherwise. None for a file only kie.ai hosts."""
        if self.is_grouped:
            return [str(p) for r in self.download_results() for p in r.output_paths]
        return [f.path for f in self.result_files()]

    def download_results(self) -> list[DownloadResult]:
        """A grouped download job's per-URL results; [] for anything else."""
        if not self.is_grouped:
            return []
        return [DownloadResult.model_validate(r) for r in self._entries]
