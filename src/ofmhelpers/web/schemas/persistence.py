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
relies on). File references are NOT a separate model -- they live inside a
job's `result` payload as they do today (a list of `{"name", "path"}` dicts,
a grouped `{"url", "success", "output_paths"}` list, or a bare id string).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobStatus(StrEnum):
    """The only three states web/stores/jobs.py ever assigns."""

    running = "running"
    done = "done"
    failed = "failed"


class Job(BaseModel):
    """A background-job record. `result`/`params`/`preview` stay as free-form
    JSON payloads (stored in a JSONB column) because their shape varies per
    task type -- see the module docstring."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    params: dict[str, Any] = {}
    actor: str | None = None
    status: JobStatus = JobStatus.running
    result: list[Any] | dict[str, Any] | str | None = None
    error: str | None = None
    created_at: float
    preview: dict[str, Any] | None = None
