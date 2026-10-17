"""Handing a stored file back to the browser.

Both helpers exist to serve user-supplied files without letting them execute
in our own origin -- see `media_response`'s content-type handling.
"""

import mimetypes
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ofmhelpers.web.routers.task_helpers.download_names import download_name
from ofmhelpers.web.routers.task_helpers.uploads import (
    MEDIA_KINDS,
    classify_kind,
)
from ofmhelpers.web.schemas import Job


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


def serve_job_file(
    job: Job | None,
    index: int,
    as_attachment: bool = True,
) -> FileResponse:
    """Generic '/files/{job_id}/{index}' implementation. The URL only ever
    carries a job id + integer index -- never a raw path -- and this only
    ever serves a file that job's own result already points to.

    Inline serving goes through media_response, so a job whose output is not
    image/video/audio comes back as a download instead of as a page in our
    own origin (see media_response)."""
    if job is None or job.status != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    # Same order job_assets numbers the files in: across every group for a
    # grouped download job, one entry per file otherwise.
    paths = (
        [str(p) for r in job.download_results() for p in r.output_paths]
        if job.is_grouped
        else [f.path for f in job.result_files()]
    )
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

    if as_attachment:
        return FileResponse(
            path,
            filename=download_name(path.name),
            media_type="application/octet-stream",
        )

    return media_response(path)
