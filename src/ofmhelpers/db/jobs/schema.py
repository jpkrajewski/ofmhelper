# Copyright (C) 2026 jpkrajewski

from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue


class DownloadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    success: bool
    output_paths: list[Path] = []
    error: str | None = None
    again_error: str | None = None


class JobStatus(StrEnum):
    running = "running"
    done = "done"
    failed = "failed"


class JobHeader(BaseModel):
    task: str
    actor: str | None


class ResultFile(BaseModel):
    name: str
    path: str | None
    remote_url: str | None = None


class JobPreview(BaseModel):
    remote_url: str
    kind: str | None = None


class GenerationRecord(BaseModel):
    part: int = 0
    provider: str
    model: str
    task_id: str
    state: str | None = None
    cost_usd: float | None = None
    seconds: float | None = None
    key_tail: str | None = None


class JobMeta(BaseModel):
    generations: list[GenerationRecord] = []


class JobCost(BaseModel):
    model_config = ConfigDict(frozen=True)

    usd: Decimal | None = None
    estimated: bool = False


class Job(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task: str
    params: dict[str, Any] = {}
    user_id: int | None = None
    status: JobStatus = JobStatus.running
    result: list[Any] | dict[str, Any] | str | None = None
    error: str | None = None
    created_at: float
    finished_at: float | None = None
    preview: JobPreview | None = None
    hidden_at: float | None = None
    meta: JobMeta = JobMeta()
    cost_usd: Decimal | None = None
    cost_estimated: bool = False
    made_by: str | None = None

    @property
    def actor(self) -> str | None:
        return None if self.user_id is None else str(self.user_id)

    @property
    def _entries(self) -> list[JsonValue]:
        result = self.result
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return result
        return []

    @property
    def is_grouped(self) -> bool:
        entries = self._entries
        first = entries[0] if entries else None
        return isinstance(first, dict) and "success" in first

    def result_files(self) -> list[ResultFile]:
        if self.is_grouped:
            return []
        return [ResultFile.model_validate(f) for f in self._entries]

    def local_paths(self) -> list[str | None]:
        if self.is_grouped:
            return [str(p) for r in self.download_results() for p in r.output_paths]
        return [f.path for f in self.result_files()]

    def download_results(self) -> list[DownloadResult]:
        if not self.is_grouped:
            return []
        return [DownloadResult.model_validate(r) for r in self._entries]


class JobPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    jobs: list[Job]
    total: int


class Created(BaseModel):
    model_config = ConfigDict(frozen=True)

    since: float | None = None
    until: float | None = None


class GalleryJobs(BaseModel):
    jobs: list[Job]
    next_offset: int | None


class JobCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor: str
    task: str
    count: int


class SpendPeriod(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    from_ts: float
    to_ts: float


class Spend(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    period: str
    runs: int
    usd: Decimal
    estimated_usd: Decimal
    unpriced: int


class UserDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    day: date
    first_login_at: float | None = None
    last_login_at: float | None = None
    logout_at: float | None = None
    idle_logout_at: float | None = None
    reels: int = 0
    usd: Decimal | None = None


class SessionSpan(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    start_at: float
    end_at: float | None = None
    next_at: float | None = None
    last_job_at: float | None = None


class RecordedJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    task: str
    user_id: int
    status: str = "done"
    created_at: float
    finished_at: float | None = None
    params: dict[str, Any] = {}
    result: list[Any] | dict[str, Any] | str | None = None
    error: str | None = None
    meta: JobMeta = JobMeta()
    cost_usd: Decimal | None = None
    cost_estimated: bool = False


class JobParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    params: dict[str, Any] = {}


class Named(BaseModel):
    model_config = ConfigDict(frozen=True)

    fragment: str
    used_at: float | None = None
    running: bool = False
    in_result: bool = False
