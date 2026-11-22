from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.db.jobs.schema import Job, JobStatus, ResultFile
from ofmhelpers.features.jobs.schema import AssetCard, error_reason
from ofmhelpers.integrations.apify.instagram import ApifyItemError, ApifyReel

__all__ = ["ApifyItemError", "ApifyReel", "SavedReel", "ViralFind"]


class SavedReel(BaseModel):
    reel: ApifyReel
    path: Path


class ViralFind(BaseModel):
    reels: list[SavedReel]
    scanned: int
    cost_usd: float


class ViralFile(ResultFile):
    code: str
    url: str
    plays: int
    likes: int | None = None
    comments: int | None = None
    posted_at: datetime | None = None
    seconds: float | None = None


class ViralReel(BaseModel):
    file: AssetCard
    code: str
    url: str
    plays: int
    likes: int | None = None
    seconds: float | None = None
    posted_at: datetime | None = None


class ViralSearch(BaseModel):
    job_id: str
    profile: str
    status: JobStatus
    error: str | None = None
    created_at: float
    scanned: int | None = None
    cost_usd: float | None = None
    reels: list[ViralReel] = []

    @property
    def error_reason(self) -> str:
        return error_reason(self.error)

    @classmethod
    def of(cls, job: Job, files: list[AssetCard]) -> ViralSearch:
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


_COUNTS = {"code", "url", "plays", "likes", "seconds", "posted_at"}


def _stored(job: Job) -> list[ViralFile]:
    if job.status != JobStatus.done or not isinstance(job.result, list):
        return []
    return [ViralFile.model_validate(entry) for entry in job.result]
