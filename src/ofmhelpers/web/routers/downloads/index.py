from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.config import settings
from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.features.downloads import sheet
from ofmhelpers.features.jobs.models import TaskCard
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Viewing
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.routers.registry import DOWNLOAD_TOOLS, task_card
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/download-assets",
    tags=["download-assets"],
    dependencies=[Access(Area.DOWNLOAD)],
)

gallery = Gallery(
    set(DOWNLOAD_TOOLS),
    limit=lambda: settings.web.download_gallery_page,
    by_use=True,
)


def _cards(jobs: list[Job]) -> list[TaskCard]:
    return [card for job in jobs for card in task_card(job).per_file()]


@router.get("")
def form(request: Request, viewer: Viewing, query: GalleryParams) -> Response:
    first = gallery.page(viewer, query)
    return render(
        request,
        "download_assets_form.html",
        gallery=_cards(first.jobs),
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(viewer),
        dates=query,
        sheet_cooldown_s=sheet.cooldown_left(viewer.user.id),
    )


@router.get("/gallery")
def gallery_page(request: Request, viewer: Viewing, query: GalleryParams) -> Response:
    found = gallery.page(viewer, query)
    return render(
        request,
        "download_gallery_fragment.html",
        gallery=_cards(found.jobs),
        next_offset=found.next_offset,
    )
