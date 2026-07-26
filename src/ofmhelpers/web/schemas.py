"""
Pydantic v2 models for the three durable stores that back the web app:
jobs (web/jobs.py), todos (web/todos.py) and approval tokens
(web/approval_tokens.py). These are the typed contract at the persistence
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
    """The only three states web/jobs.py ever assigns."""

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


class Todo(BaseModel):
    """A VA task-list row. Defaults match todos.add_todo, so a partial record
    (e.g. one written by import_todos, which omits the asset/approval fields)
    validates too."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    model_name: str
    url: str
    comments: str = ""
    checked: bool = False
    created_at: float
    created_by: str | None = None
    asset_path: str | None = None
    asset_name: str | None = None
    approved: bool = False
    rejected: bool = False
    reject_comment: str | None = None
    drive_file_id: str | None = None
    drive_uploaded_at: float | None = None
    drive_upload_job_id: str | None = None


class ApprovalToken(BaseModel):
    """A single-use magic-link approval token (routers/approve.py). Snapshots
    the asset_path it was issued for so a later asset swap is caught as
    'stale' rather than approving the wrong file."""

    model_config = ConfigDict(from_attributes=True)

    token: str
    todo_id: str
    asset_path: str
    created_at: float
    expires_at: float
    used_at: float | None = None
