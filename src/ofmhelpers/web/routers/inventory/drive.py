from typing import Annotated

from fastapi import APIRouter, Path, Request, Response

from ofmhelpers.db.drive_folders.schema import SyncReport
from ofmhelpers.features.handmade import service as handmade
from ofmhelpers.features.repurpose import service as repurpose
from ofmhelpers.features.storage import jobs as storage_jobs
from ofmhelpers.features.storage.schema import Source
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import (
    Access,
    Actors,
    CurrentUser,
    Scope,
    Storage,
)
from ofmhelpers.web.responses import PRIVATE_FOR_A_DAY
from ofmhelpers.web.routers.generation.clone import FILES_PREFIX as CLONE_FILES
from ofmhelpers.web.routers.generation.clone import TASK as CLONE_TASK
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/drive", tags=["drive"], dependencies=[Access(Area.DRIVE)])

FileId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{10,128}$")]

_SOURCES = [
    Source(task=CLONE_TASK, files_prefix=CLONE_FILES, page="/clone"),
    Source(
        task=repurpose.TASK, files_prefix=repurpose.FILES_PREFIX, page=repurpose.PREFIX
    ),
    Source(
        task=handmade.TASK, files_prefix=handmade.FILES_PREFIX, page=handmade.PREFIX
    ),
]


@router.get("")
def drive_page(
    request: Request,
    storage: Storage,
    user: CurrentUser,
    actors: Actors,
    upload: str = "",
) -> Response:
    view = storage.page.view(user.id, actors, upload, _SOURCES)
    return render(request, "drive.html", dict(view))


@router.get("/live")
def drive_live(request: Request, storage: Storage, user: CurrentUser) -> Response:
    board = storage.folders.board(viewer=user.id)
    return render(request, "_drive_board.html", {"board": board})


@router.post("/sync")
def sync(scope: Scope) -> SyncReport:
    return storage_jobs.sync(owner=scope)


@router.get("/thumb/{login_id}/{file_id}")
def thumb(login_id: int, file_id: FileId, storage: Storage) -> Response:
    found = storage.page.preview(login_id, file_id)
    media_type = (
        found.media_type if found.media_type.startswith("image/") else "image/jpeg"
    )
    return Response(
        content=found.content,
        media_type=media_type,
        headers={**PRIVATE_FOR_A_DAY, "X-Content-Type-Options": "nosniff"},
    )
