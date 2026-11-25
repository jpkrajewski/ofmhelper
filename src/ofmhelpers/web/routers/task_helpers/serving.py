# Copyright (C) 2026 jpkrajewski

import logging
import mimetypes
from http import HTTPStatus
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.features.assets.names import download_name
from ofmhelpers.features.downloads import again as downloads
from ofmhelpers.features.previews import render as previews
from ofmhelpers.web.routers.task_helpers.uploads import MEDIA_KINDS, classify_kind

logger = logging.getLogger(__name__)

_IMMUTABLE = "private, max-age=31536000, immutable"


def media_response(path: Path, filename: str | None = None) -> FileResponse:
    headers = {"X-Content-Type-Options": "nosniff"}
    name = filename or path.name
    if classify_kind(name) in MEDIA_KINDS:
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, headers=headers)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,
        headers=headers,
    )


REMOVED = "Removed to save space: download it again"


def _result_path(job: Job | None, index: int) -> Path:
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    paths = job.local_paths()
    if index < 0 or index >= len(paths):
        raise HTTPException(status_code=404, detail="File not found")

    raw_path = paths[index]
    if raw_path is None:
        raise HTTPException(status_code=404, detail="File was never downloaded locally")

    return Path(raw_path)


def job_file_path(job: Job | None, index: int) -> Path:
    path = _result_path(job, index)
    if not path.is_file():
        if job is not None and downloads.removed(job.id, index):
            raise HTTPException(status_code=HTTPStatus.GONE, detail=REMOVED)
        raise HTTPException(status_code=404, detail="File no longer exists on server")
    return path


def _kept_thumbnail(job: Job | None, index: int, kind: str) -> FileResponse | None:
    if job is None or job.status != "done":
        return None
    paths = job.local_paths()
    raw_path = paths[index] if 0 <= index < len(paths) else None
    if raw_path is None:
        return None
    source = Path(raw_path)
    if source.is_file() or classify_kind(source.name) != kind:
        return None
    kept = (
        previews.poster_path(job.id, index)
        if kind == "video"
        else previews.preview_path(job.id, index, kind)
    )
    if not kept.is_file():
        return None
    return FileResponse(
        kept,
        media_type="image/webp",
        headers={"Cache-Control": _IMMUTABLE},
    )


def serve_job_file(
    job: Job | None,
    index: int,
    as_attachment: bool = True,  # noqa: FBT001, FBT002
) -> FileResponse:
    path = job_file_path(job, index)
    if as_attachment:
        response = FileResponse(
            path,
            filename=download_name(path.name, job.id if job else ""),
            media_type="application/octet-stream",
        )
    else:
        response = media_response(path)
    response.headers["Cache-Control"] = _IMMUTABLE
    return response


class _ResultMedia(BaseModel):
    job: Job
    path: Path


def _result_media(job: Job | None, index: int, kinds: set[str]) -> _ResultMedia:
    path = job_file_path(job, index)
    if job is None or classify_kind(path.name) not in kinds:
        raise HTTPException(status_code=404, detail="No preview for this file")
    return _ResultMedia(job=job, path=path)


def serve_job_preview(
    job: Job | None,
    index: int,
    files_prefix: str,
) -> FileResponse | RedirectResponse:
    kept = _kept_thumbnail(job, index, "image")
    if kept is not None:
        return kept
    media = _result_media(job, index, {"image", "video"})
    job, path = media.job, media.path
    kind = classify_kind(path.name)
    if kind == "video" and not previews.preview_path(job.id, index, kind).is_file():
        return RedirectResponse(
            f"{files_prefix}/{job.id}/{index}",
            status_code=HTTPStatus.TEMPORARY_REDIRECT,
            headers={"Cache-Control": "no-store"},
        )
    try:
        dest = previews.ResultPreview(source=path, job_id=job.id, index=index).make()
    except previews.FailedBeforeError:  # silent: failed before: the original
        return media_response(path)
    except previews.PREVIEW_ERRORS:
        logger.warning("could not preview %s", path, exc_info=True)
        return media_response(path)
    media_type = "video/mp4" if dest.suffix == ".mp4" else "image/webp"
    return FileResponse(
        dest,
        media_type=media_type,
        headers={"Cache-Control": _IMMUTABLE},
    )


def serve_job_poster(job: Job | None, index: int) -> FileResponse:
    kept = _kept_thumbnail(job, index, "video")
    if kept is not None:
        return kept
    media = _result_media(job, index, {"video"})
    job, path = media.job, media.path
    try:
        dest = previews.ResultPreview(source=path, job_id=job.id, index=index).poster()
    except previews.PREVIEW_ERRORS:
        logger.warning("could not make a poster of %s", path, exc_info=True)
        raise HTTPException(status_code=404, detail="No poster") from None
    return FileResponse(
        dest,
        media_type="image/webp",
        headers={"Cache-Control": _IMMUTABLE},
    )
