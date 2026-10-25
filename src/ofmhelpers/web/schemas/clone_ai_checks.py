"""A AI check as the app keeps it (`clone_ai_checks`), and a /clone-links
result's way to Drive (`clone_from_links`)."""

from enum import StrEnum

from pydantic import BaseModel

from ofmhelpers.clone_ai_check import CloneAiCheck


class StoredCloneAiCheck(CloneAiCheck):
    """A `CloneAiCheck` with its row: id, whose, the result it checked (if
    one), when."""

    id: int
    user_id: int | None = None
    job_id: str | None = None
    file_index: int | None = None
    created_at: float


class ReviewStatus(StrEnum):
    CHECKING = "checking"
    UPLOADING = "uploading"
    DONE = "done"
    FAILED = "failed"


class CloneFromLink(BaseModel):
    """One result checked, then uploaded into GOOD REELS or TRIAL REELS
    (web/clone_links/review.py). `check` is its check, when it got one."""

    id: int
    job_id: str
    file_index: int
    user_id: int
    status: ReviewStatus
    check_id: int | None = None
    folder_name: str | None = None
    drive_file_id: str | None = None
    drive_url: str | None = None
    error: str | None = None
    created_at: float
    finished_at: float | None = None
    check: StoredCloneAiCheck | None = None

    @property
    def running(self) -> bool:
        return self.status in (ReviewStatus.CHECKING, ReviewStatus.UPLOADING)

    def cut_off(self, now: float, stale_s: float) -> bool:
        """Still running long after it started: its worker was killed."""
        return self.running and self.created_at < now - stale_s
