"""
ofmhelpers/web/routers/downloads/index.py

Unified page for Download videos / Download images / Clean images, in the
same style as /generate: one form with a tool picker on the left, and a
non-blocking gallery of past runs across all three tools on the right --
`download_gallery_page` cards first, the rest fetched on scroll
(gallery-scroll.js, `/download-assets/gallery`). Submissions POST to each
tool's existing /run endpoint.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from ofmhelpers.config import settings
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.registry import DOWNLOAD_TOOLS, task_card
from ofmhelpers.web.schemas import TaskCard
from ofmhelpers.web.stores.jobs import list_jobs_page
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/download-assets", tags=["download-assets"])


def _gallery_page(request: Request, offset: int) -> tuple[list[TaskCard], int | None]:
    """One page of cards plus the next page's offset, None after the last
    (what ends the scroll)."""
    size = settings.web.download_gallery_page
    page, total = list_jobs_page(
        set(DOWNLOAD_TOOLS), offset, size, AuthMiddleware.actor(request)
    )
    next_offset = offset + size
    return [task_card(j) for j in page], next_offset if next_offset < total else None


@router.get("")
def form(request: Request):
    gallery, next_offset = _gallery_page(request, 0)
    return render(
        request,
        "download_assets_form.html",
        gallery=gallery,
        next_offset=next_offset,
    )


@router.get("/gallery")
def gallery_page(request: Request, offset: Annotated[int, Query(ge=0)] = 0):
    """The next page of cards as a fragment gallery-scroll.js appends: the
    same partial the page renders."""
    gallery, next_offset = _gallery_page(request, offset)
    return render(
        request,
        "download_gallery_fragment.html",
        gallery=gallery,
        next_offset=next_offset,
    )
