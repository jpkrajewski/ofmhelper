# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, File, Request
from fastapi.responses import Response

from ofmhelpers.config import settings
from ofmhelpers.features.assets.models import VideoUpload
from ofmhelpers.features.clone.service import drive_picker
from ofmhelpers.features.handmade import service
from ofmhelpers.features.handmade.service import TASK, run_handmade
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.web.access import Action, Area
from ofmhelpers.web.dependencies import Access, Can, Storage, Viewing
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render

__all__ = ["TASK", "router", "run_handmade"]

router = APIRouter(
    prefix=service.PREFIX,
    tags=["handmade"],
    dependencies=[Access(Area.HANDMADE)],
)

gallery = Gallery({TASK}, limit=lambda: settings.web.download_gallery_page)


@router.get("")
def page(
    request: Request,
    viewer: Viewing,
    storage: Storage,
    query: GalleryParams,
) -> Response:
    first = gallery.page(viewer, query)
    return render(
        request,
        "handmade.html",
        {
            "gallery": service.cards(first.jobs),
            "next_offset": first.next_offset,
            "hidden_count": gallery.hidden_count(viewer),
            "dates": query,
            "drive_filter": True,
            "max_files": settings.web.handmade_max_files,
            **dict(drive_picker(storage.uploads)),
        },
    )


@router.get("/gallery")
def gallery_page(
    request: Request,
    viewer: Viewing,
    storage: Storage,
    query: GalleryParams,
) -> Response:
    found = gallery.page(viewer, query)
    return render(
        request,
        "handmade_gallery_fragment.html",
        {
            "gallery": service.cards(found.jobs),
            "next_offset": found.next_offset,
            **dict(drive_picker(storage.uploads)),
        },
    )


@router.post("/run", status_code=202, dependencies=[Can(Action.UPLOAD_HANDMADE)])
def run(
    viewer: Viewing,
    files: Annotated[list[VideoUpload] | None, File(media_type="video/*")] = None,
) -> JobsQueued:
    return service.start(files or [], viewer.user.id)


add_job_routes(
    router,
    service.LABEL,
    lambda _job: "Saving…",
    back_url=service.PREFIX,
    back_label="upload more reels",
)
