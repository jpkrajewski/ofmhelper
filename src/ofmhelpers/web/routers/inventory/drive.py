"""
/drive: each phone owner's own Google Drive "ALL ACCOUNTS" folder
(web/drive_folders.py). A card per Instagram login on an active phone (a
signed-in person's own phones, admins included),
with what waits in its folder (previews for images and videos, proxied
through `/drive/thumb/{login}/{id}`: the owner's token never leaves the
server)
and what was posted (its POSTED folder: now, today, last 7 days). Below,
the root's folders no login owns, to clean up. Sync runs the
sweep now instead of at the next interval.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response

from ofmhelpers.gdrive import DriveError
from ofmhelpers.log import get_logger
from ofmhelpers.signin import SignInError
from ofmhelpers.web import drive_folders
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.generation.clone import FILES_PREFIX as CLONE_FILES
from ofmhelpers.web.routers.generation.clone import TASK as CLONE_TASK
from ofmhelpers.web.routers.task_helpers import job_assets
from ofmhelpers.web.schemas import SyncReport, UploadPick
from ofmhelpers.web.stores import drive_uploads as upload_store
from ofmhelpers.web.stores.jobs import get_job
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)

router = APIRouter(prefix="/drive", tags=["drive"])

# Drive ids: letters, digits, - and _.
FileId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{10,128}$")]
# A file's preview never changes; the browser keeps it, nobody else.
_THUMB_CACHE = {"Cache-Control": "private, max-age=86400"}


def _upload_pick(request: Request, job_id: str) -> UploadPick | None:
    """The /clone video `?upload=` names, when it is the session's own
    finished run."""
    job = get_job(job_id, AuthMiddleware.actor(request))
    if job is None or job.task != CLONE_TASK or job.status != "done":
        return None
    assets, _failed = job_assets(job, CLONE_FILES)
    if not assets:
        return None
    first = assets[0]
    return UploadPick(
        job_id=job.id,
        name=first.download_name,
        preview_url=first.preview_url or first.view_url,
        poster_url=first.poster_url,
        already=[
            u.login_id
            for u in upload_store.for_jobs([job.id])
            if u.status in ("done", "queued")
        ],
    )


@router.get("")
def drive_page(request: Request, upload: str = ""):
    """At once, from each folder's last read: no Drive call. drive.js then
    swaps in `/drive/live`. `upload`: a /clone run whose video goes into
    the account card clicked (drive.js)."""
    board = drive_folders.board(
        owner=AuthMiddleware.scope(request),
        viewer=AuthMiddleware.user(request).id,
        live=False,
    )
    pick = _upload_pick(request, upload) if upload else None
    return render(request, "drive.html", board=board, pick=pick)


@router.get("/live")
def drive_live(request: Request):
    """The cards read from Drive now (~3 Google round trips), as the
    fragment the page swaps in."""
    board = drive_folders.board(
        owner=AuthMiddleware.scope(request), viewer=AuthMiddleware.user(request).id
    )
    return render(request, "_drive_board.html", board=board)


@router.post("/sync")
def sync(request: Request) -> SyncReport:
    """The session's own folders now, admins included (the background sweep
    does everyone's); `not_connected` names them if their Google Drive is
    not connected. The report names only their own folders."""
    return drive_folders.sync(AuthMiddleware.scope(request))


@router.get("/thumb/{login_id}/{file_id}")
def thumb(request: Request, login_id: int, file_id: FileId) -> Response:
    try:
        found = drive_folders.thumbnail(
            login_id, file_id, AuthMiddleware.scope(request)
        )
    except (DriveError, SignInError) as exc:
        logger.warning("drive: preview of %s failed: %s", file_id, exc)
        raise HTTPException(status_code=404, detail="No preview") from exc
    if found is None:
        raise HTTPException(status_code=404, detail="No preview yet")
    content, media_type = found
    return Response(content=content, media_type=media_type, headers=_THUMB_CACHE)
