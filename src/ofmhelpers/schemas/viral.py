"""/viral: a search's stored reels and the page's view of a search."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from ofmhelpers.schemas.errors import error_reason
from ofmhelpers.schemas.jobs import AssetCard
from ofmhelpers.schemas.persistence import Job, JobStatus, ResultFile


class ViralFile(ResultFile):
    """One downloaded reel in a search's stored result: the file, plus what
    Instagram counted for it."""

    code: str
    url: str
    plays: int
    likes: int | None = None
    comments: int | None = None
    posted_at: datetime | None = None
    seconds: float | None = None

    def to_result(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_unset=True)


class ViralReel(BaseModel):
    """One reel card: its file (play/preview/download URLs) and counts."""

    file: AssetCard
    url: str
    plays: int
    likes: int | None = None
    seconds: float | None = None
    posted_at: datetime | None = None


class ViralSearch(BaseModel):
    """One search (a `viral_reels` job) as the page shows it."""

    job_id: str
    profile: str
    status: JobStatus
    error: str | None = None
    created_at: float
    # How many reels Apify read, and what that cost.
    scanned: int | None = None
    cost_usd: float | None = None
    reels: list[ViralReel] = []

    @property
    def error_reason(self) -> str:
        return error_reason(self.error)

    @classmethod
    def of(cls, job: Job, files: list[AssetCard]) -> ViralSearch:
        """`job` with `files` (its result's asset cards, in result order)."""
        reels = [
            ViralReel(file=file, **stored.model_dump(include=_COUNTS))
            for stored, file in zip(_stored(job), files, strict=False)
        ]
        return cls(
            job_id=job.id,
            profile=job.params.get("profile", ""),
            status=job.status,
            error=job.error,
            created_at=job.created_at,
            scanned=job.params.get("scanned"),
            cost_usd=job.params.get("cost_usd"),
            reels=reels,
        )


_COUNTS = {"url", "plays", "likes", "seconds", "posted_at"}


def _stored(job: Job) -> list[ViralFile]:
    if job.status != JobStatus.done or not isinstance(job.result, list):
        return []
    return [ViralFile.model_validate(entry) for entry in job.result]
