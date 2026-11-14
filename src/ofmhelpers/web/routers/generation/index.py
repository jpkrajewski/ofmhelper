"""
/generate: one form for every model of every provider -- provider, then
model, then the model's own settings (loaded from /ai/fields) -- posting to
/ai/run, and a non-blocking gallery of past generations that a click reloads
back into the form.
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.auth.access import Area
from ofmhelpers.config import settings
from ofmhelpers.features.generate.page import form_view
from ofmhelpers.web.dependencies import Access, Actor, ApiKeys, Viewing
from ofmhelpers.web.gallery import Gallery, GalleryParams
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
    return render(
        request,
        "generate_form.html",
        api_keys=api_keys,
        gallery=[task_card(j) for j in first.jobs],
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(viewer),
        dates=query,
        **dict(form_view(actor)),
    )


@router.get("/gallery")
def gallery_page(request: Request, viewer: Viewing, query: GalleryParams) -> Response:
    """The next page of gallery cards as a fragment gallery-scroll.js appends --
    same partial the page's first page renders, so an appended card is
    indistinguishable from a server-rendered one. `hidden`: only the hidden
    cards (hidden-cards.js's "Show hidden")."""
    found = gallery.page(viewer, query)
    return render(
        request,
        "generate_gallery_fragment.html",
        gallery=[task_card(j) for j in found.jobs],
        next_offset=found.next_offset,
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request, viewer: Viewing, query: GalleryParams) -> Response:
    """The newest page of cards, no sentinel: what live-gallery.js polls to
    prepend runs started in other sessions."""
    return render_polled(
        request,
        "generate_gallery_fragment.html",
        gallery=[task_card(j) for j in gallery.page(viewer, query.newest()).jobs],
        next_offset=None,
    )
