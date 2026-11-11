"""
/drive: each phone owner's own Google Drive "ALL ACCOUNTS" folder
(features/drive/folders.py). A card per Instagram login on an active phone (a
signed-in person's own phones, admins included),
with what waits in its folder (previews for images and videos, proxied
through `/drive/thumb/{login}/{id}`: the owner's token never leaves the
server)
and what was posted (its POSTED folder: now, today, last 7 days). Below,
the root's folders no login owns, to clean up. Sync runs the
sweep now instead of at the next interval.
"""

from typing import Annotated

from fastapi import APIRouter, Path, Request, Response

from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.drive import page
from ofmhelpers.schemas import SyncReport
from ofmhelpers.web.dependencies import Actor, CurrentUser, Scope
from ofmhelpers.web.responses import PRIVATE_FOR_A_DAY
from ofmhelpers.web.routers.generation.clone import FILES_PREFIX as CLONE_FILES
from ofmhelpers.web.routers.generation.clone import TASK as CLONE_TASK
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/drive", tags=["drive"])

# Drive ids: letters, digits, - and _.
FileId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{10,128}$")]

# `?upload=` names a finished /clone run.
_CLONE = page.Source(task=CLONE_TASK, files_prefix=CLONE_FILES)


@router.get("")
def drive_page(
    request: Request, scope: Scope, user: CurrentUser, actor: Actor, upload: str = ""
) -> Response:
    """At once, from each folder's last read: no Drive call. drive.js then
    swaps in `/drive/live`. `upload`: a /clone run whose video goes into
    the account card clicked (drive.js)."""
    view = page.page_view(scope, user.id, actor, upload, _CLONE)
    return render(request, "drive.html", **dict(view))


@router.get("/live")
def drive_live(request: Request, scope: Scope, user: CurrentUser) -> Response:
    """The cards read from Drive now (~3 Google round trips), as the
    fragment the page swaps in."""
    board = drive_folders.board(owner=scope, viewer=user.id)
    return render(request, "_drive_board.html", board=board)


@router.post("/sync")
def sync(scope: Scope) -> SyncReport:
    """The session's own folders now, admins included (the background sweep
    does everyone's); `not_connected` names them if their Google Drive is
    not connected. The report names only their own folders."""
    return drive_folders.sync(scope)


@router.get("/thumb/{login_id}/{file_id}")
def thumb(login_id: int, file_id: FileId, scope: Scope) -> Response:
    found = page.preview(login_id, file_id, scope)
    # A file's preview never changes; the browser keeps it, nobody else.
    return Response(
        content=found.content, media_type=found.media_type, headers=PRIVATE_FOR_A_DAY
    )
