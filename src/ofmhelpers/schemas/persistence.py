"""
Pydantic v2 models for the job store that backs the web app
(db/stores/jobs.py). These are the typed contract at the persistence
boundary -- one source of truth for the shapes that used to live only as
ad-hoc dicts in JSON files.

Schemas only: no business logic, no DB, no I/O. The SQLAlchemy models
(db/models.py) and the repository layer (db/repository.py) bridge to
these via `model_validate(row)` thanks to `from_attributes=True`.

Field sets mirror exactly what the current code writes, so an existing
`uploads/*.json` record validates unchanged (that's what the step-5 backfill
relies on). File references are NOT a separate table -- they live inside a
job's `result` payload (a list of `ResultFile` dumps, a grouped list of
`DownloadResult` dumps, or a bare id string); `Job.result_files()` /
`Job.download_results()` read them back as models.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from ofmhelpers.schemas.downloads import DownloadResult


class JobStatus(StrEnum):
    """The only three states db/stores/jobs.py ever assigns."""

    running = "running"
    done = "done"
    failed = "failed"


class JobHeader(BaseModel):
    """Which task a job is and who started it."""

    task: str
    actor: str | None


class ResultFile(BaseModel):
    """One output file in a job's stored `result` (the one-entry-per-file
    shape; grouped download results are `downloaders` models). `path` is None
    when only kie.ai's hosted copy exists; `remote_url` is set only by the
    kie.ai tools. Stored with `exclude_unset` (`db/stores/jobs.py`), so a
    field never set stays out of the JSON exactly as before this was a model."""

    name: str
    path: str | None
    remote_url: str | None = None


class JobPreview(BaseModel):
    """kie.ai's hosted result, published while the job is still downloading
    it locally -- the page plays it from kie.ai until the job is done.
    `kind` is "image"/"video"; older previews were stored without it."""

    remote_url: str
    kind: str | None = None


class GenerationRecord(BaseModel):
    """One provider request of a job, as it ended."""

    part: int = 0
    provider: str
    model: str
    task_id: str
    state: str | None = None
    cost_usd: float | None = None
    seconds: float | None = None
    # The end of the key it ran on, as /account shows a saved one: which of
    # a person's keys paid, never the key.
    key_tail: str | None = None


class JobMeta(BaseModel):
    """What happened on a job's way (`jobs.meta`): its provider requests.
    What they cost together is the job's own `cost_usd` column."""

    generations: list[GenerationRecord] = []


class JobCost(BaseModel):
    """What a run cost in dollars (`jobs.cost_usd`): None = no known price,
    never 0. `estimated`: the provider reported none, the amount is the
    app's own price for the run."""

    model_config = ConfigDict(frozen=True)

    usd: Decimal | None = None
    estimated: bool = False


class Job(BaseModel):
    """A background-job record. `params` and `result` are free-form JSON
    (JSONB) because their shape varies per task type -- `result` is read back
    through `result_files()` / `download_results()`."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    params: dict[str, Any] = {}
    # Whose job it is; None = nobody's (a system event, a removed user's).
    user_id: int | None = None
    status: JobStatus = JobStatus.running
    result: list[Any] | dict[str, Any] | str | None = None
    error: str | None = None
    created_at: float
    finished_at: float | None = None
    preview: JobPreview | None = None
    # When its owner hid it from the galleries; None = shown.
    hidden_at: float | None = None
    meta: JobMeta = JobMeta()
    # What the run cost; None = no known price. `cost_estimated`: from the
    # app's own prices, the provider reported none.
    cost_usd: Decimal | None = None
    cost_estimated: bool = False
    # Who made it, for a reader it is not their own (a VA's job on its
    # owner's gallery); set by the read, never stored.
    made_by: str | None = None

    @property
    def actor(self) -> str | None:
        """`user_id` as worker kwargs, Redis keys and the audit log name
        the person."""
        return None if self.user_id is None else str(self.user_id)

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


class JobPage(BaseModel):
    """One page of jobs, newest first, and how many match in total (before
    the slice: whether another page exists)."""

    model_config = ConfigDict(frozen=True)

    jobs: list[Job]
    total: int


class GalleryJobs(BaseModel):
    """One page of jobs. No `next_offset` is what ends the infinite scroll."""

    jobs: list[Job]
    next_offset: int | None


class JobCount(BaseModel):
    """How many done jobs of `task` `actor` (a user's id, as text) has."""

    model_config = ConfigDict(frozen=True)

    actor: str
    task: str
    count: int


class SpendPeriod(BaseModel):
    """One period of `user_id`'s runs to sum (`from_ts` <= created_at <
    `to_ts`, epoch seconds), under the name the page gives it."""

    model_config = ConfigDict(frozen=True)

    name: str
    from_ts: float
    to_ts: float


class Spend(BaseModel):
    """What `user_id`'s runs of one period cost: how many ran, the dollars
    known (`usd`), how much of that is the app's estimate (`estimated_usd`)
    and how many runs had no price at all."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    period: str
    runs: int
    usd: Decimal
    estimated_usd: Decimal
    unpriced: int


class JobParams(BaseModel):
    """One job's stored `params`, exactly as stored."""

    model_config = ConfigDict(frozen=True)

    params: dict[str, Any] = {}


class Named(BaseModel):
    """A text (`fragment`: a file's digest, a name) some job names: `used_at`
    = when the newest job whose params hold it was started (None: only a
    result holds it), `running` = one of those still runs or waits,
    `in_result` = a job's result holds it."""

    model_config = ConfigDict(frozen=True)

    fragment: str
    used_at: float | None = None
    running: bool = False
    in_result: bool = False
