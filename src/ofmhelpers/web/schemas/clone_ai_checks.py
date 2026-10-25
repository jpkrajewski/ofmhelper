"""A AI check as the app keeps it (`clone_ai_checks`), and a /clone-links
result's way to Drive (`clone_from_links`)."""

from enum import StrEnum

from pydantic import BaseModel

from ofmhelpers.clone_ai_check import CloneAiCheck, Verdict


class ReviewReason(StrEnum):
    """Why a result is not in Drive, as the card says it after "Not in
    Drive:" (web/clone_links/review.py). Fixed words only: the raw error
    goes to the log and the span, never to a page. A Drive refusal's
    reason comes from `web.drive_space.upload_error`."""

    NOT_CONNECTED = "Connect Google Drive on your Account page first"
    NOT_SAVED = "The video was not saved here: use Download"
    NOT_STARTED = "The AI check did not start"
    QUEUE_FAILED = "Could not start the AI check"
    VIDEO_GONE = "The video file is gone"
    CUT_OFF = "Cut off by a restart"
    TIMED_OUT = "Took too long"
    UNEXPECTED = "Could not finish: use Download"


# What a check that raised (not an LLM failure: those come back as a check)
# says on its card; it still goes to TRIAL REELS.
CHECK_FAILED = "AI check failed"


class StoredCloneAiCheck(CloneAiCheck):
    """A `CloneAiCheck` with its row: id, whose, the result it checked (if
    one), when."""

    id: int
    user_id: int | None = None
    job_id: str | None = None
    file_index: int | None = None
    created_at: float

    @property
    def decision(self) -> str:
        """What the card's button says after "AI decision:"."""
        if self.verdict is None:
            if self.error_kind == "quota":
                return "not done (AI busy or daily limit reached)"
            return "not done"
        return "good" if self.verdict is Verdict.GOOD else "glitch"

    @property
    def shown_error(self) -> str | None:
        """Why there is no verdict, as the card's panel shows it: `error`
        (a few safe words) when the check kept its kind, else a fixed line
        -- a row from before `error_kind` may hold the provider's raw text."""
        if not self.error:
            return None
        return self.error if self.error_kind else CHECK_FAILED


class ReviewStatus(StrEnum):
    CHECKING = "checking"
    UPLOADING = "uploading"
    DONE = "done"
    FAILED = "failed"


class CloneFromLink(BaseModel):
    """One result checked, then uploaded into GOOD REELS or TRIAL REELS
    (web/clone_links/review.py). `check` is its check, when it got one.
    `path`: the result's file; `started_at`: when a worker claimed it (None:
    waiting in the queue), also the claim's token -- a write of an older
    claim is ignored; `attempts`: how many times a worker claimed it."""

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
    path: str | None = None
    attempts: int = 0
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    check: StoredCloneAiCheck | None = None

    @property
    def running(self) -> bool:
        return self.status in (ReviewStatus.CHECKING, ReviewStatus.UPLOADING)

    def cut_off(self, now: float, stale_s: float) -> bool:
        """Claimed by a worker longer than `stale_s` ago and still running:
        that worker was killed (recovery queues it again). One not claimed
        yet is waiting in the queue, never cut off."""
        return (
            self.running
            and self.started_at is not None
            and self.started_at < now - stale_s
        )
