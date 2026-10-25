"""A finished /clone-links run's video on its way to Drive: checked for
glitches (`ofmhelpers.clone_ai_check`), then uploaded as its owner's Google
account into their root (`drive_folders.root_name()`), GOOD REELS or
TRIAL REELS (`folders.py`).

`start_reviews` (end of the run's job) starts one `clone_from_links` row per
result and queues `review_file` for each: the generated video is already
saved by then, so nothing here can lose it or fail its job. Each review
writes its check (`clone_ai_checks`: verdict, reasoning, prompt, raw answer,
timings) and one Logfire span `AI check {job_id}` with the verdict,
glitch percent, folder and any error; a failure is the row's `error`,
shown on the card, where "Check again" starts it over.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING

import logfire

from ofmhelpers.cache import enqueue
from ofmhelpers.clone_ai_check import check_clone
from ofmhelpers.gdrive import DriveError
from ofmhelpers.log import get_logger
from ofmhelpers.signin import SignInError
from ofmhelpers.web.clone_links.folders import folder_for
from ofmhelpers.web.download_names import download_name
from ofmhelpers.web.drive_folders import review_target
from ofmhelpers.web.drive_space import upload_error
from ofmhelpers.web.schemas.clone_ai_checks import ReviewStatus
from ofmhelpers.web.stores import clone_ai_checks
from ofmhelpers.web.stores import clone_from_links as store
from ofmhelpers.web.stores.users import refresh_token

if TYPE_CHECKING:
    from ofmhelpers.web.schemas.clone_ai_checks import CloneFromLink

logger = get_logger(__name__)

NOT_CONNECTED = "Connect Google Drive on your Account page first"


def start_reviews(job_id: str, user_id: int, paths: list[str | None]) -> list[int]:
    """A review per local result (`paths` in result order), queued; the ids
    started. One already done or running is left alone. Never raises."""
    started = []
    for index, path in enumerate(paths):
        if path is None:
            continue
        try:
            review_id = store.start(job_id, index, user_id)
        except Exception:
            logger.exception("AI check of %s/%s not started", job_id, index)
            continue
        if review_id is None:
            continue
        started.append(review_id)
        try:
            enqueue(review_file, review_id, path)
        except Exception as exc:
            logger.warning("AI check %s not queued", review_id, exc_info=True)
            store.finish(review_id, error=f"Could not queue the check: {exc}")
    return started


def review_file(review_id: int, path: str) -> None:
    """Worker job: check one result, then upload it. Any failure ends as
    the row's `error`, never left running."""
    review = store.get(review_id)
    if review is None or review.status is not ReviewStatus.CHECKING:
        return
    with logfire.span(
        "AI check {job_id}",
        job_id=review.job_id,
        file_index=review.file_index,
        review_id=review_id,
        user_id=review.user_id,
    ) as span:
        try:
            folder = _check(review, Path(path), span)
            _upload(review, Path(path), folder, span)
        except Exception as exc:
            logger.warning("AI check %s failed", review_id, exc_info=True)
            store.finish(review_id, error=f"Review failed: {exc}")
            span.record_exception(exc)
            span.set_level("error")


def _check(review: CloneFromLink, video: Path, span: logfire.LogfireSpan) -> str:
    """The check, saved; the folder it sends the video to."""
    check = check_clone(video)
    saved = clone_ai_checks.save(
        check,
        user_id=review.user_id,
        job_id=review.job_id,
        file_index=review.file_index,
    )
    folder = folder_for(check)
    store.checked(review.id, saved.id, folder)
    span.set_attributes(
        {
            "check_id": saved.id,
            "verdict": check.verdict or "",
            "glitch_percent": check.glitch_percent,
            "folder": folder,
            "provider": check.provider,
            "model": check.model,
            "check_error": check.error or "",
        }
    )
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
    token = refresh_token(review.user_id)
    if not token:
        store.finish(review.id, error=NOT_CONNECTED)
        return
    mime = mimetypes.guess_type(video.name)[0] or "application/octet-stream"
    try:
        drive, root = review_target(token, review.user_id)
        target = drive.ensure_folder(root, folder)
        made = drive.upload(target.id, video, download_name(video.name), mime)
    except (DriveError, SignInError) as exc:
        logger.warning("AI check %s: upload failed: %s", review.id, exc)
        span.set_attribute("upload_error", str(exc))
        store.finish(review.id, error=upload_error(review.user_id, exc))
        return
    store.finish(review.id, drive_file_id=made.id, drive_url=made.url)
    span.set_attribute("drive_file_id", made.id)
