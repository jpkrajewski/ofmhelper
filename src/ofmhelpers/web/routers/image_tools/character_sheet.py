"""
/character-sheet: upload several shots of one character (typically 9:16),
get them back as one lossless PNG, side by side in upload order. Admin only;
its sheets land in the admin part of the asset store. The work:
`features.character_sheet.service`.
"""

from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response

from ofmhelpers.config import settings
from ofmhelpers.features.character_sheet import service
from ofmhelpers.features.character_sheet.service import (
    FILES_PREFIX,
    PREFIX,
    TASK,
)

# Jobs queued before 2026-11-11 name the run function by this module's path.
from ofmhelpers.features.character_sheet.service import run_combine as _run_combine
from ofmhelpers.schemas import JobQueued
from ofmhelpers.schemas.access import Area
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render

__all__ = ["FILES_PREFIX", "TASK", "_run_combine", "router"]

router = APIRouter(
    prefix=PREFIX, tags=["character-sheet"], dependencies=[Access(Area.CHARACTER_SHEET)]
)


@router.get("")
def form(request: Request, actor: Actor) -> Response:
    return render(
        request,
        "character_sheet.html",
        gallery=service.cards(actor),
        max_images=settings.web.character_sheet_max_images,
    )


@router.post("/run", status_code=202)
def run(
    actor: Actor,
    assets_root: AssetsRoot,
    files: Annotated[list[UploadFile] | None, File()] = None,
) -> JobQueued:
    return service.start(files or [], actor, assets_root)


add_job_routes(
    router,
    "Character sheet",
    lambda job: f"Combining {len(job.params['files'])} image(s)…",
    back_url="/character-sheet",
    back_label="make another sheet",
)
