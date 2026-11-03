"""
ofmhelpers/web/routers/downloads/index.py

Unified page for Download videos / Download images / Clean images, in the
same style as /generate: one form with a tool picker on the left, and a
non-blocking gallery of past runs across all three tools on the right --
`download_gallery_page` cards first, the rest fetched on scroll
(gallery-scroll.js, `/download-assets/gallery`). Submissions POST to each
tool's existing /run endpoint.
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.config import settings
from ofmhelpers.schemas import Job, TaskCard
from ofmhelpers.web.dependencies import Actor
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.routers.registry import DOWNLOAD_TOOLS, task_card
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/download-assets", tags=["download-assets"])

gallery = Gallery(set(DOWNLOAD_TOOLS), limit=lambda: settings.web.download_gallery_page)


def _cards(jobs: list[Job]) -> list[TaskCard]:
    """Every file of every job its own card: a download of six is six cards."""
    return [card for job in jobs for card in task_card(job).per_file()]


@router.get("")
def form(request: Request, actor: Actor) -> Response:
    first = gallery.page(actor)
    return render(
        request,
        "download_assets_form.html",
        gallery=_cards(first.jobs),
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(actor),
    )


@router.get("/gallery")
def gallery_page(request: Request, actor: Actor, query: GalleryParams) -> Response:
    """The next page of cards as a fragment gallery-scroll.js appends: the
    same partial the page renders. `hidden`: only the hidden cards."""
    found = gallery.page(actor, query)
    return render(
        request,
        "download_gallery_fragment.html",
        gallery=_cards(found.jobs),
        next_offset=found.next_offset,
    )
