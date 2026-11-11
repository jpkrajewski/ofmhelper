"""Handing a stored file back to the browser.

Both helpers exist to serve user-supplied files without letting them execute
in our own origin -- see `media_response`'s content-type handling.
"""

import mimetypes
from http import HTTPStatus
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from ofmhelpers.features.assets.names import download_name
from ofmhelpers.features.downloads import again as downloads
from ofmhelpers.features.previews import render as previews
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import Job
from ofmhelpers.web.routers.task_helpers.uploads import MEDIA_KINDS, classify_kind

logger = get_logger(__name__)

# A job's results never change once it is done, so neither do their URLs'
# answers: a second look at a card costs no transfer at all.
_IMMUTABLE = "private, max-age=31536000, immutable"


def media_response(path: Path, filename: str | None = None) -> FileResponse:
    """Serve a user-uploaded file without letting it run inside our origin.

    Image/video/audio go back inline with their real type -- the app's own
    <img>/<video> tags and Discord's link crawler both need that. Anything
    else is forced to `application/octet-stream` as an attachment: uploads
    are restricted to media now, but files stored before that (or reached by
    some path this doesn't know about) must not be able to come back as
    same-origin HTML and run script with the viewer's session cookie.
    `nosniff` stops the browser overriding either decision.
    """
    headers = {"X-Content-Type-Options": "nosniff"}
    name = filename or path.name
    if classify_kind(name) in MEDIA_KINDS:
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, headers=headers)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=name,  # Content-Disposition: attachment
        headers=headers,
    )


# A video the downloads sweep removed (features/downloads/cleanup.py): 410,
# which download.js turns into the card's poster and its Download button.
REMOVED = "Removed to save space: download it again"


def _result_path(job: Job | None, index: int) -> Path:
    """Where the job's result says file `index` is; it may be gone."""
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    paths = job.local_paths()
    if index < 0 or index >= len(paths):
        raise HTTPException(status_code=404, detail="File not found")

    raw_path = paths[index]
    if raw_path is None:
        # Local download never completed -- this asset only ever existed as
        # a remote_url (see job_status_payload), which the client already
        # has and points its view/download links at directly. There's
        # nothing local for this route to serve.
        raise HTTPException(status_code=404, detail="File was never downloaded locally")

    return Path(raw_path)


def job_file_path(job: Job | None, index: int) -> Path:
    """The local file behind '/files/{job_id}/{index}'. The URL only ever
    carries a job id + integer index -- never a raw path -- and this only
    ever resolves a file that job's own result already points to."""
    path = _result_path(job, index)
    if not path.is_file():
        if job is not None and downloads.removed(job.id, index):
            raise HTTPException(status_code=HTTPStatus.GONE, detail=REMOVED)
        raise HTTPException(status_code=404, detail="File no longer exists on server")
    return path


def _kept_thumbnail(job: Job | None, index: int, kind: str) -> FileResponse | None:
    """What stays of a result whose file is gone (a removed download keeps
    its poster, an image its small preview): served as it is, no source
    needed. None while the file is there, or nothing was kept."""
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
        kept, media_type="image/webp", headers={"Cache-Control": _IMMUTABLE}
    )


def serve_job_file(
    job: Job | None,
    index: int,
    as_attachment: bool = True,
) -> FileResponse:
    """Generic '/files/{job_id}/{index}' implementation.

    Inline serving goes through media_response, so a job whose output is not
    image/video/audio comes back as a download instead of as a page in our
    own origin (see media_response)."""
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


def _result_media(job: Job | None, index: int, kinds: set[str]) -> tuple[Job, Path]:
    path = job_file_path(job, index)
    if job is None or classify_kind(path.name) not in kinds:
        raise HTTPException(status_code=404, detail="No preview for this file")
    return job, path


def serve_job_preview(
    job: Job | None, index: int, files_prefix: str
) -> FileResponse | RedirectResponse:
    """'/files/{job_id}/{index}/preview': a result image or video small
    enough for a slow connection (see features/previews/render.py) -- made by the
    preview worker when the job finished. A video's not made yet, or one that
    cannot be made: sent on to the original's own URL (a slow picture beats
    none), never the original's bytes here: once the small copy is made this
    URL serves it, and a player still holding the original's length asks
    for ranges it has not got (416s, prod 2026-11-07). An image's is made now
    (milliseconds)."""
    kept = _kept_thumbnail(job, index, "image")
    if kept is not None:
        return kept
    job, path = _result_media(job, index, {"image", "video"})
    kind = classify_kind(path.name)
    if kind == "video" and not previews.preview_path(job.id, index, kind).is_file():
        return RedirectResponse(
            f"{files_prefix}/{job.id}/{index}",
            status_code=HTTPStatus.TEMPORARY_REDIRECT,
            headers={"Cache-Control": "no-store"},
        )
    try:
        dest = previews.ResultPreview(path, job.id, index).make()
    except previews.FailedBeforeError:
        return media_response(path)
    except previews.PREVIEW_ERRORS:
        logger.warning("could not preview %s", path, exc_info=True)
        return media_response(path)
    media_type = "video/mp4" if dest.suffix == ".mp4" else "image/webp"
    return FileResponse(
        dest, media_type=media_type, headers={"Cache-Control": _IMMUTABLE}
    )


def serve_job_poster(job: Job | None, index: int) -> FileResponse:
    """'/files/{job_id}/{index}/poster': a result video's first frame, so a
    gallery shows every card without loading any video."""
    kept = _kept_thumbnail(job, index, "video")
    if kept is not None:
        return kept
    job, path = _result_media(job, index, {"video"})
    try:
        dest = previews.ResultPreview(path, job.id, index).poster()
    except previews.PREVIEW_ERRORS:
        logger.warning("could not make a poster of %s", path, exc_info=True)
        raise HTTPException(status_code=404, detail="No poster") from None
    return FileResponse(
        dest, media_type="image/webp", headers={"Cache-Control": _IMMUTABLE}
    )
