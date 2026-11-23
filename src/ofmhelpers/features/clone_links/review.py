# Copyright (C) 2026 jpkrajewski

import mimetypes
import time
from pathlib import Path
from types import MappingProxyType
lazy from collections.abc import Callable

import logfire
from rq.timeouts import JobTimeoutException

from ofmhelpers.cache import enqueue, get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.clone_ai_checks import store as clone_ai_checks
from ofmhelpers.db.clone_ai_checks.schema import (
    CHECK_FAILED,
    ReviewReason,
    ReviewStatus,
)
from ofmhelpers.db.clone_from_links import store
from ofmhelpers.db.jobs.store import create_job, fail_job, get_job
from ofmhelpers.features.assets.names import download_name
from ofmhelpers.features.clone_links.ai_check import check_clone, unanswered
from ofmhelpers.features.clone_links.constants import (
    CloneLinksTask,
    clone_links_constants,
)
from ofmhelpers.features.clone_links.folders import folder_for
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.storage.client import saved_storage, storage_of
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.storage import (
    RECONNECT,
    DriveError,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.clone_ai_checks.schema import CloneFromLink
lazy from ofmhelpers.db.drive_folders.schema import DriveFile
lazy from ofmhelpers.features.clone_links.ai_check import CloneAiCheck
lazy from ofmhelpers.features.storage.service import StorageService
lazy from ofmhelpers.integrations.storage import StorageClient

logger = get_logger(__name__)


def start_reviews(job_id: str, user_id: int, paths: list[str | None]) -> list[int]:
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
    job_id = None
    try:
        job_id = _job(review_id)
        enqueue(
            run_job,
            (job_id, review_file, {"review_id": review_id}),
            job_timeout=settings.clone_links.review_timeout_s,
        )
    except Exception:
        logger.warning("AI check %s not queued", review_id, exc_info=True)
        store.finish(review_id, claimed=None, error=ReviewReason.QUEUE_FAILED)
        if job_id is not None:
            fail_job(job_id, clone_links_constants().queue_failed_job)


def _job(review_id: int) -> str:
    review = store.get(review_id)
    return create_job(
        CloneLinksTask.REVIEW,
        {
            "review_id": review_id,
            "clone_job": review.job_id if review else None,
            "file_index": review.file_index if review else None,
        },
        actor=str(review.user_id) if review else None,
    )


def review_file(review_id: int, path: str | None = None) -> None:
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
            storage = storage_of(review.user_id, interactive=False)
            _review(review, review.path or path, span, storage)
        except JobTimeoutException as exc:
            logger.exception("AI check %s ran out of time", review_id)
            _failed(review, ReviewReason.TIMED_OUT, exc, span)
        except Exception as exc:
            logger.exception("AI check %s failed", review_id)
            _failed(review, ReviewReason.UNEXPECTED, exc, span)


def _review(
    review: CloneFromLink,
    path: str | None,
    span: logfire.LogfireSpan,
    storage: StorageService,
) -> None:
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
    _upload(review, video, folder, span, storage)


def _gone(review: CloneFromLink, span: logfire.LogfireSpan) -> None:
    logger.warning("AI check %s: no file at %s", review.id, review.path)
    span.set_level("error")
    _finish(review, error=ReviewReason.VIDEO_GONE)


def _failed(
    review: CloneFromLink,
    reason: str,
    exc: Exception,
    span: logfire.LogfireSpan,
) -> None:
    span.record_exception(exc)
    span.set_level("error")
    try:
        _finish(review, error=reason)
    except Exception:
        logger.exception("AI check %s: failure not saved", review.id)


def _mirrored(job_id: str) -> bool:
    job = get_job(job_id)
    return bool(job and job.params.get("mirror"))


def _checked(
    review: CloneFromLink,
    video: Path,
    span: logfire.LogfireSpan,
) -> CloneAiCheck:
    try:
        return check_clone(video, mirrored=_mirrored(review.job_id))
    except JobTimeoutException:
        raise
    except Exception as exc:
        logger.exception("AI check %s raised", review.id)
        span.record_exception(exc)
        return unanswered(video, CHECK_FAILED)


def _saved(review: CloneFromLink, check: CloneAiCheck) -> int | None:
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
        },
    )
    if check.error:
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
    review: CloneFromLink,
    video: Path,
    folder: str,
    span: logfire.LogfireSpan,
    storage: StorageService,
) -> None:
    if not video.is_file():
        _gone(review, span)
        return
    if storage.review.client is None:
        _finish(review, error=ReviewReason.NOT_CONNECTED)
        return
    name = download_name(video.name)
    mime = mimetypes.guess_type(video.name)[0] or "application/octet-stream"
    try:
        place = storage.review.folder(folder)
        drive, target = place.drive, place.folder_id
        made = (
            _already_there(drive, target, name, video.stat().st_size)
            if review.attempts > 1
            else None
        )
        if made is None:
            made = drive.upload(target, video, name, mime)
        else:
            span.set_attributes({"adopted": True})
    except (DriveError, SignInError) as exc:
        logger.warning("AI check %s: upload failed: %s", review.id, exc, exc_info=True)
        span.set_attribute("upload_error", str(exc))
        span.set_level("error")
        _finish(review, error=storage.space.upload_error(exc))
        return
    _finish(review, drive_file_id=made.id, drive_url=made.url)
    span.set_attribute("drive_file_id", made.id)


def _already_there(
    drive: StorageClient,
    folder_id: str,
    name: str,
    size: int,
) -> DriveFile | None:
    return next(
        (
            f
            for f in drive.children(folder_id)
            if f.name == name and f.size in (None, size) and not f.is_folder
        ),
        None,
    )


def _finish(
    review: CloneFromLink,
    *,
    drive_file_id: str | None = None,
    drive_url: str | None = None,
    error: str | None = None,
) -> None:
    if not store.finish(
        review.id,
        claimed=review.started_at,
        drive_file_id=drive_file_id,
        drive_url=drive_url,
        error=error,
    ):
        logger.warning("AI check %s: row taken over, result not saved", review.id)


def resume_reviews() -> MappingProxyType[str, str]:
    s = settings.clone_links
    now = time.time()
    since = now - settings.aigen.resume_max_age_s
    outcomes: dict[str, str] = {}
    for review in store.cut_off(now - s.review_stale_s):
        outcomes[f"review {review.id}"] = _guarded(_resume_cut_off, review)
    for review in store.never_claimed(now - s.review_lost_s):
        outcomes[f"review {review.id}"] = _guarded(_resume_lost, review)
    for review in store.failed_since(since):
        if _retryable(review):
            outcomes[f"review {review.id}"] = _guarded(_resume_failed, review)
    for job in store.unreviewed_jobs(CloneLinksTask.RUN, since):
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
    return MappingProxyType(outcomes)


def _guarded(step: Callable[[CloneFromLink], str], review: CloneFromLink) -> str:
    try:
        return step(review)
    except Exception:
        logger.exception("recovery: review %s not resumed", review.id)
        return "error"


def _resume_cut_off(review: CloneFromLink) -> str:
    if review.attempts >= settings.clone_links.review_attempts:
        _finish(review, error=ReviewReason.CUT_OFF)
        logger.warning("recovery: review %s cut off too often: failed", review.id)
        return "gave up"
    if not store.release(review):
        return "changed"
    _queue(review.id)
    logger.warning("recovery: review %s was cut off: queued again", review.id)
    return "requeued"


def _resume_lost(review: CloneFromLink) -> str:
    if time.time() - review.created_at > settings.aigen.resume_max_age_s:
        _finish(review, error=ReviewReason.CUT_OFF)
        logger.warning("recovery: review %s never started: failed", review.id)
        return "gave up"
    ttl = settings.clone_links.review_lost_s
    requeued_key = clone_links_constants().requeued_key.format(review_id=review.id)
    if not get_redis().set(requeued_key, "1", nx=True, ex=ttl):
        return "waiting"
    _queue(review.id)
    logger.warning("recovery: review %s never started: queued again", review.id)
    return "requeued"


def _retryable(review: CloneFromLink) -> bool:
    if review.attempts >= settings.clone_links.review_attempts:
        return False
    if review.error == ReviewReason.QUEUE_FAILED:
        return True
    return review.folder_name is not None and review.error not in ReviewReason.final()


def _resume_failed(review: CloneFromLink) -> str:
    drive_full = saved_storage(review.user_id).constants.drive_full
    if review.error in {drive_full, RECONNECT}:
        space = storage_of(review.user_id, interactive=False).space.cached()
        if space is None or space.low or space.reconnect:
            return "waiting for Drive"
    if not store.reopen(review):
        return "changed"
    _queue(review.id)
    logger.info("recovery: review %s (%s) tried again", review.id, review.error)
    return "retried"
