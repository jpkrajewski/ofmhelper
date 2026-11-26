# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, File, Request
from fastapi.responses import Response
from pydantic import AfterValidator

from ofmhelpers.config import settings
from ofmhelpers.features.assets.models import ImageUpload
from ofmhelpers.features.character_sheet import service
from ofmhelpers.features.character_sheet.service import (
    FILES_PREFIX,
    PREFIX,
    TASK,
)
from ofmhelpers.features.character_sheet.service import run_combine as _run_combine
from ofmhelpers.features.jobs.schema import JobQueued
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render

__all__ = ["FILES_PREFIX", "TASK", "_run_combine", "router"]

router = APIRouter(
    prefix=PREFIX,
    tags=["character-sheet"],
    dependencies=[Access(Area.CHARACTER_SHEET)],
)


def _within_limits(files: list[ImageUpload]) -> list[ImageUpload]:
    minimum = settings.web.character_sheet_min_images
    maximum = settings.web.character_sheet_max_images
    if not minimum <= len(files) <= maximum:
        msg = f"Upload between {minimum} and {maximum} files"
        raise ValueError(msg)
    return files


@router.get("")
def form(request: Request, actor: Actor) -> Response:
    return render(
        request,
        "character_sheet.html",
        {
            "gallery": service.cards(actor),
            "max_images": settings.web.character_sheet_max_images,
        },
    )


@router.post("/run", status_code=202)
def run(
    actor: Actor,
    assets_root: AssetsRoot,
    files: Annotated[
        list[ImageUpload],
        File(media_type="image/*"),
        AfterValidator(_within_limits),
    ],
) -> JobQueued:
    return service.start(files, actor, assets_root)


add_job_routes(
    router,
    "Character sheet",
    lambda job: f"Combining {len(job.params['files'])} image(s)…",
    back_url="/character-sheet",
    back_label="make another sheet",
)
