from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.config import settings
from ofmhelpers.features.generate.page import form_view
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Actor, ApiKeys, Viewing
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.responses import NO_STORE
from ofmhelpers.web.routers.registry import GENERATE_TOOLS, task_card
from ofmhelpers.web.templates_config import render, render_polled

router = APIRouter(
    prefix="/generate", tags=["generate"], dependencies=[Access(Area.GENERATE)]
)

gallery = Gallery(GENERATE_TOOLS, limit=lambda: settings.web.gallery_limit)


@router.get("")
def form(
    request: Request,
    actor: Actor,
    viewer: Viewing,
    api_keys: ApiKeys,
    query: GalleryParams,
) -> Response:
    first = gallery.page(viewer, query)
    response = render(
        request,
        "generate_form.html",
        api_keys=api_keys,
        gallery=[task_card(j) for j in first.jobs],
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(viewer),
        dates=query,
        **dict(form_view(actor)),
    )
    response.headers.update(NO_STORE)
    return response


@router.get("/gallery")
def gallery_page(request: Request, viewer: Viewing, query: GalleryParams) -> Response:
    found = gallery.page(viewer, query)
    return render(
        request,
        "generate_gallery_fragment.html",
        gallery=[task_card(j) for j in found.jobs],
        next_offset=found.next_offset,
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request, viewer: Viewing, query: GalleryParams) -> Response:
    return render_polled(
        request,
        "generate_gallery_fragment.html",
        gallery=[task_card(j) for j in gallery.page(viewer, query.newest()).jobs],
        next_offset=None,
    )
