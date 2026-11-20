from typing import Annotated

from fastapi import APIRouter, Path, Request, Response

from ofmhelpers.db.drive_folders.schema import SyncReport
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.drive import page
from ofmhelpers.features.handmade import service as handmade
from ofmhelpers.features.repurpose import service as repurpose
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Actors, CurrentUser, Scope
from ofmhelpers.web.responses import PRIVATE_FOR_A_DAY
from ofmhelpers.web.routers.generation.clone import FILES_PREFIX as CLONE_FILES
from ofmhelpers.web.routers.generation.clone import TASK as CLONE_TASK
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/drive", tags=["drive"], dependencies=[Access(Area.DRIVE)])

FileId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{10,128}$")]

_SOURCES = [
    page.Source(task=CLONE_TASK, files_prefix=CLONE_FILES, page="/clone"),
    page.Source(
        task=repurpose.TASK, files_prefix=repurpose.FILES_PREFIX, page=repurpose.PREFIX
    ),
    page.Source(
        task=handmade.TASK, files_prefix=handmade.FILES_PREFIX, page=handmade.PREFIX
    ),
]


@router.get("")
def drive_page(
    request: Request,
    scope: Scope,
    user: CurrentUser,
    actors: Actors,
    upload: str = "",
) -> Response:
    view = page.page_view(scope, user.id, actors, upload, _SOURCES)
    return render(request, "drive.html", **dict(view))


@router.get("/live")
def drive_live(request: Request, scope: Scope, user: CurrentUser) -> Response:
    board = drive_folders.board(owner=scope, viewer=user.id)
    return render(request, "_drive_board.html", board=board)


@router.post("/sync")
def sync(scope: Scope) -> SyncReport:
    return drive_folders.sync(scope)


@router.get("/thumb/{login_id}/{file_id}")
def thumb(login_id: int, file_id: FileId, scope: Scope) -> Response:
    found = page.preview(login_id, file_id, scope)
    media_type = (
        found.media_type if found.media_type.startswith("image/") else "image/jpeg"
    )
    return Response(
        content=found.content,
        media_type=media_type,
        headers={**PRIVATE_FOR_A_DAY, "X-Content-Type-Options": "nosniff"},
    )
