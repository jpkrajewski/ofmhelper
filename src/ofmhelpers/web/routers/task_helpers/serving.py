"""Handing a stored file back to the browser.

Both helpers exist to serve user-supplied files without letting them execute
in our own origin -- see `media_response`'s content-type handling.
"""

import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ofmhelpers.log import get_logger
from ofmhelpers.web import previews
from ofmhelpers.web.routers.task_helpers.download_names import download_name
from ofmhelpers.web.routers.task_helpers.uploads import (
    MEDIA_KINDS,
    classify_kind,
)
from ofmhelpers.web.schemas import Job

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


def job_file_path(job: Job | None, index: int) -> Path:
    """The local file behind '/files/{job_id}/{index}'. The URL only ever
    carries a job id + integer index -- never a raw path -- and this only
    ever resolves a file that job's own result already points to."""
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

    path = Path(raw_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")
    return path


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
            filename=download_name(path.name),
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


def serve_job_preview(job: Job | None, index: int) -> FileResponse:
    """'/files/{job_id}/{index}/preview': a result image or video small
    enough for a slow connection (see web/previews.py) -- usually made when
    the job finished, else now. If it cannot be made, the original is served
    instead: a slow picture beats none."""
    job, path = _result_media(job, index, {"image", "video"})
    try:
        dest = previews.make_preview(path, job.id, index)
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
    job, path = _result_media(job, index, {"video"})
    try:
        dest = previews.make_poster(path, job.id, index)
    except previews.PREVIEW_ERRORS:
        logger.warning("could not make a poster of %s", path, exc_info=True)
        raise HTTPException(status_code=404, detail="No poster") from None
    return FileResponse(
        dest, media_type="image/webp", headers={"Cache-Control": _IMMUTABLE}
    )
