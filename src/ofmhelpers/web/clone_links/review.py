"""A finished /clone-links run's video on its way to Drive: checked for
glitches (`ofmhelpers.clone_ai_check`), then uploaded as its owner's Google
account into their root (`drive_folders.root_name()`), GOOD REELS or
TRIAL REELS (`folders.py`).

`start_reviews` (end of the run's job) starts one `clone_from_links` row per
result and queues `review_file` for each: the generated video is already
saved by then, so nothing here can lose it or fail its job. A worker first
claims the row (a second queued job for it does nothing), then writes the
check (`clone_ai_checks`: verdict, reasoning, prompt, raw answer, timings)
and one Logfire span `AI check {job_id}` with the verdict, glitch percent,
folder and any error. A check that cannot answer, or raises, still sends
the video to TRIAL REELS. A failure ends as the row's `error`: a fixed
reason (`ReviewReason`) the card shows after "Not in Drive:"; the raw text
goes to the log and the span only.

The card has no retry button (user decision 2026-10-25: the VA sorts in
Drive). `resume_reviews`, recovery's step every sweep, retries instead: a review
whose worker was killed is queued again, an upload that failed for a
reason that may pass is tried again with the check it already has, and a
done run whose review never started gets one -- each a bounded number of
times (`settings.clone_links.review_attempts`).
"""

from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import TYPE_CHECKING

import logfire
from rq.timeouts import JobTimeoutException

from ofmhelpers.cache import enqueue
from ofmhelpers.clone_ai_check import check_clone, unanswered
from ofmhelpers.config import settings
from ofmhelpers.gdrive import RECONNECT, DriveError
from ofmhelpers.log import get_logger
from ofmhelpers.signin import SignInError
from ofmhelpers.web.clone_links.folders import folder_for
from ofmhelpers.web.download_names import download_name
from ofmhelpers.web.drive_folders import review_folder
from ofmhelpers.web.drive_space import DRIVE_FULL, drive_space, upload_error
from ofmhelpers.web.schemas.clone_ai_checks import (
    CHECK_FAILED,
    ReviewReason,
    ReviewStatus,
)
from ofmhelpers.web.stores import clone_ai_checks
from ofmhelpers.web.stores import clone_from_links as store
from ofmhelpers.web.stores.jobs import get_job
from ofmhelpers.web.stores.users import refresh_token

if TYPE_CHECKING:
    from collections.abc import Callable

    from ofmhelpers.clone_ai_check import CloneAiCheck
    from ofmhelpers.gdrive import DriveApi, DriveFile
    from ofmhelpers.web.schemas.clone_ai_checks import CloneFromLink

logger = get_logger(__name__)

NOT_CONNECTED: str = ReviewReason.NOT_CONNECTED.value
# The run whose results these are (routers/generation/clone_links.py TASK).
RUN_TASK = "clone_links"
# A failed review is tried again unless it failed for one of these: nothing
# a retry changes (the file is gone, Drive is not connected, it was given
# up on already).
_FINAL = frozenset(
    {
        ReviewReason.NOT_CONNECTED,
        ReviewReason.NOT_SAVED,
        ReviewReason.VIDEO_GONE,
        ReviewReason.CUT_OFF,
    }
)
# Tried again only once Drive has room / takes the person's token again.
_WAIT_FOR_DRIVE = frozenset({DRIVE_FULL, RECONNECT})


def start_reviews(job_id: str, user_id: int, paths: list[str | None]) -> list[int]:
    """A review per result (`paths` in result order), queued; the ids
    started. One already done or running is left alone; a result kept only
    by the provider (path None) gets a failed one saying so. Never raises."""
    started = []
    for index, path in enumerate(paths):
        try:
            review_id = _start(job_id, index, user_id, path)
        except Exception:
            logger.exception("AI check of %s/%s not started", job_id, index)
            continue
        if review_id is not None:
            started.append(review_id)
    return started


def _start(job_id: str, index: int, user_id: int, path: str | None) -> int | None:
    review_id = store.start(job_id, index, user_id, path)
    if review_id is None:
        return None
    if path is None:
        logger.warning("AI check %s: %s/%s has no local file", review_id, job_id, index)
        store.finish(review_id, claimed=None, error=ReviewReason.NOT_SAVED)
    else:
        _queue(review_id)
    return review_id


def _queue(review_id: int) -> None:
    """`review_file` queued; a queue that refuses it fails the row (recovery
    tries again)."""
    try:
        enqueue(
            review_file, review_id, job_timeout=settings.clone_links.review_timeout_s
        )
    except Exception:
        logger.warning("AI check %s not queued", review_id, exc_info=True)
        store.finish(review_id, claimed=None, error=ReviewReason.QUEUE_FAILED)


def review_file(review_id: int, path: str | None = None) -> None:
    """Worker job: claim the review, check its video, then upload it. A
    review already uploading (its check saved) is only uploaded. `path`:
    the file, for a job queued before the row kept it."""
    review = store.claim(review_id)
    if review is None:
        return
    with logfire.span(
        "AI check {job_id}",
        job_id=review.job_id,
        file_index=review.file_index,
        review_id=review_id,
        user_id=review.user_id,
        attempt=review.attempts,
    ) as span:
        try:
            _review(review, review.path or path, span)
        except JobTimeoutException as exc:
            # RQ's time limit: its worker is killed shortly, so no upload.
            logger.exception("AI check %s ran out of time", review_id)
            _failed(review, ReviewReason.TIMED_OUT, exc, span)
        except Exception as exc:
            logger.exception("AI check %s failed", review_id)
            _failed(review, ReviewReason.UNEXPECTED, exc, span)


def _review(review: CloneFromLink, path: str | None, span: logfire.LogfireSpan) -> None:
    video = Path(path) if path else None
    if video is None or not video.is_file():
        _gone(review, span)
        return
    resumed = review.status is ReviewStatus.UPLOADING and review.folder_name is not None
    folder = review.folder_name if resumed else _check(review, video, span)
    if folder is None:
        return
    if resumed:
        span.set_attributes({"folder": folder, "resumed": True})
    _upload(review, video, folder, span)


def _gone(review: CloneFromLink, span: logfire.LogfireSpan) -> None:
    logger.warning("AI check %s: no file at %s", review.id, review.path)
    span.set_level("error")
    _finish(review, error=ReviewReason.VIDEO_GONE)


def _failed(
    review: CloneFromLink, reason: str, exc: Exception, span: logfire.LogfireSpan
) -> None:
    span.record_exception(exc)
    span.set_level("error")
    try:
        _finish(review, error=reason)
    except Exception:
        logger.exception("AI check %s: failure not saved", review.id)


def _mirrored(job_id: str) -> bool:
    """Whether the run's export flipped its video left-right."""
    job = get_job(job_id)
    return bool(job and job.params.get("mirror"))


def _checked(
    review: CloneFromLink, video: Path, span: logfire.LogfireSpan
) -> CloneAiCheck:
    """The check of `video`; one with no verdict when the check itself
    raised (a non-LLM failure: those come back as a check)."""
    try:
        return check_clone(video, mirrored=_mirrored(review.job_id))
    except JobTimeoutException:
        raise
    except Exception as exc:
        logger.exception("AI check %s raised", review.id)
        span.record_exception(exc)
        return unanswered(video, CHECK_FAILED)


def _saved(review: CloneFromLink, check: CloneAiCheck) -> int | None:
    """The check's row id; None when it could not be saved (its job was
    deleted meanwhile: the review row went with it)."""
    try:
        saved = clone_ai_checks.save(
            check,
            user_id=review.user_id,
            job_id=review.job_id,
            file_index=review.file_index,
        )
    except JobTimeoutException:
        raise
    except Exception:
        logger.exception("AI check %s: check not saved", review.id)
        return None
    return saved.id


def _check(review: CloneFromLink, video: Path, span: logfire.LogfireSpan) -> str | None:
    """The check, saved; the folder it sends the video to. None when the
    row is no longer this claim's: nothing more to do."""
    check = _checked(review, video, span)
    check_id = _saved(review, check)
    folder = folder_for(check)
    if not store.checked(review, check_id, folder):
        logger.warning("AI check %s: row taken over, not uploaded", review.id)
        return None
    span.set_attributes(
        {
            "check_id": check_id or 0,
            "verdict": check.verdict or "",
            "glitch_percent": check.glitch_percent,
            "folder": folder,
            "provider": check.provider,
            "model": check.model,
            "check_error": check.error or "",
            "check_error_detail": check.error_detail or "",
            "error_kind": check.error_kind or "",
            "input_tokens": check.input_tokens or 0,
            "thinking_tokens": check.thinking_tokens or 0,
            "output_tokens": check.output_tokens or 0,
        }
    )
    if check.error:
        # No verdict: the reel goes to TRIAL REELS unchecked. The check off
        # for every reel (quota, setup) or one that raised is an error, so
        # Logfire's error triage shows it; any other no-answer a warning.
        off = check.error_kind in ("quota", "setup") or check.error == CHECK_FAILED
        span.set_attribute("check_skipped", check.error_kind or "failed")
        span.set_level("error" if off else "warn")
    logger.info(
        "AI check %s (%s/%s): %s %s%% -> %s%s",
        review.id,
        review.job_id,
        review.file_index,
        check.verdict or "unchecked",
        check.glitch_percent if check.glitch_percent is not None else "-",
        folder,
        f" ({check.error})" if check.error else "",
    )
    return folder


def _upload(
    review: CloneFromLink, video: Path, folder: str, span: logfire.LogfireSpan
) -> None:
    if not video.is_file():
        _gone(review, span)
        return
    token = refresh_token(review.user_id)
    if not token:
        _finish(review, error=NOT_CONNECTED)
        return
    name = download_name(video.name)
    mime = mimetypes.guess_type(video.name)[0] or "application/octet-stream"
    try:
        drive, target = review_folder(token, review.user_id, folder)
        # A run before this one may have uploaded it before it was killed.
        made = _already_there(drive, target, name) if review.attempts > 1 else None
        if made is None:
            made = drive.upload(target, video, name, mime)
        else:
            span.set_attributes({"adopted": True})
    except (DriveError, SignInError) as exc:
        logger.warning("AI check %s: upload failed: %s", review.id, exc)
        span.set_attribute("upload_error", str(exc))
        span.set_level("error")
        _finish(review, error=upload_error(review.user_id, exc))
        return
    _finish(review, drive_file_id=made.id, drive_url=made.url)
    span.set_attribute("drive_file_id", made.id)


def _already_there(drive: DriveApi, folder_id: str, name: str) -> DriveFile | None:
    return next(
        (f for f in drive.children(folder_id) if f.name == name and not f.is_folder),
        None,
    )


def _finish(
    review: CloneFromLink,
    *,
    drive_file_id: str | None = None,
    drive_url: str | None = None,
    error: str | None = None,
) -> None:
    """The claimed row finished; nothing when it is no longer this claim's."""
    if not store.finish(
        review.id,
        claimed=review.started_at,
        drive_file_id=drive_file_id,
        drive_url=drive_url,
        error=error,
    ):
        logger.warning("AI check %s: row taken over, result not saved", review.id)


# --- recovery ------------------------------------------------------------------


def resume_reviews() -> dict[str, str]:
    """Recovery's step for reviews (`web/recovery.py`, every sweep): every
    review a deploy, OOM or crash cut off, or whose upload failed for a
    reason that may pass, queued again; a done run that has no review
    started; "review <id>" / "job <id>" -> what happened. Never raises for
    one review."""
    s = settings.clone_links
    now = time.time()
    since = now - settings.aigen.resume_max_age_s
    outcomes: dict[str, str] = {}
    for review in store.cut_off(now - s.review_stale_s):
        outcomes[f"review {review.id}"] = _guarded(_resume_cut_off, review)
    for review in store.failed_since(since):
        if _retryable(review):
            outcomes[f"review {review.id}"] = _guarded(_resume_failed, review)
    for job in store.unreviewed_jobs(RUN_TASK, since):
        owner = int(job.actor) if job.actor and job.actor.isdigit() else None
        if owner is None:
            continue
        try:
            started = start_reviews(job.id, owner, job.local_paths())
        except Exception:
            logger.exception("recovery: review of job %s not started", job.id)
            continue
        if started:
            logger.warning("recovery: job %s had no review: started", job.id)
            outcomes[f"job {job.id}"] = "review started"
    return outcomes


def _guarded(step: Callable[[CloneFromLink], str], review: CloneFromLink) -> str:
    try:
        return step(review)
    except Exception:
        logger.exception("recovery: review %s not resumed", review.id)
        return "error"


def _resume_cut_off(review: CloneFromLink) -> str:
    """Its worker was killed: queued again, or given up after
    `review_attempts` claims."""
    if review.attempts >= settings.clone_links.review_attempts:
        _finish(review, error=ReviewReason.CUT_OFF)
        logger.warning("recovery: review %s cut off too often: failed", review.id)
        return "gave up"
    if not store.release(review):
        return "changed"
    _queue(review.id)
    logger.warning("recovery: review %s was cut off: queued again", review.id)
    return "requeued"


def _retryable(review: CloneFromLink) -> bool:
    """A failed review a retry may fix: one that never got queued, or an
    upload whose check is saved, failed for a reason that may pass, with
    attempts left."""
    if review.attempts >= settings.clone_links.review_attempts:
        return False
    if review.error == ReviewReason.QUEUE_FAILED:
        return True
    return review.folder_name is not None and review.error not in _FINAL


def _resume_failed(review: CloneFromLink) -> str:
    if review.error in _WAIT_FOR_DRIVE:
        # Only once a page has seen this Drive with room and its token taken
        # again (cached only: never asks Google from here).
        space = drive_space(review.user_id, ask=False)
        if space is None or space.low or space.reconnect:
            return "waiting for Drive"
    if not store.reopen(review):
        return "changed"
    _queue(review.id)
    logger.info("recovery: review %s (%s) tried again", review.id, review.error)
    return "retried"
