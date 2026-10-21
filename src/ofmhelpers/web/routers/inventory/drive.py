"""
/drive: the shared Google Drive folder the VA uploads into
(web/drive_folders.py). A card per Instagram login on an active phone,
with what is in today's folder: previews for images and videos (proxied
through `/drive/thumb/{id}`, the account's token never leaves the server),
a link for the rest. Below, the root's folders no login owns, to clean up.
Sync makes whatever today is missing now instead of at the next sweep.
Everyone signed in sees and syncs it.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response

from ofmhelpers.gdrive import DriveError
from ofmhelpers.log import get_logger
from ofmhelpers.web import drive_folders
from ofmhelpers.web.schemas import SyncReport
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)

router = APIRouter(prefix="/drive", tags=["drive"])

# Drive ids: letters, digits, - and _.
FileId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{10,128}$")]
# A day's preview never changes; the browser keeps it, nobody else.
_THUMB_CACHE = {"Cache-Control": "private, max-age=86400"}


@router.get("")
def drive_page(request: Request):
    return render(request, "drive.html", board=drive_folders.board())


@router.post("/sync")
def sync() -> SyncReport:
    try:
        return drive_folders.sync()
    except DriveError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/thumb/{file_id}")
def thumb(file_id: FileId) -> Response:
    try:
        found = drive_folders.thumbnail(file_id)
    except DriveError as exc:
        logger.warning("drive: preview of %s failed: %s", file_id, exc)
        raise HTTPException(status_code=404, detail="No preview") from exc
    if found is None:
        raise HTTPException(status_code=404, detail="No preview yet")
    content, media_type = found
    return Response(content=content, media_type=media_type, headers=_THUMB_CACHE)
