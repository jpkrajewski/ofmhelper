"""
/generate: one form for every model of every provider -- provider, then
model, then the model's own settings (loaded from /ai/fields) -- posting to
/ai/run, and a non-blocking gallery of past generations that a click reloads
back into the form.
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.config import settings
from ofmhelpers.schemas import ModelChoice, ProviderChoice
from ofmhelpers.web.dependencies import Actor, ApiKeys
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.routers.generation.ai import MAX_BATCH
from ofmhelpers.web.routers.registry import GENERATE_TOOLS, task_card
from ofmhelpers.web.templates_config import render, render_polled

router = APIRouter(prefix="/generate", tags=["generate"])

GALLERY_LIMIT = settings.web.gallery_limit

gallery = Gallery(GENERATE_TOOLS, limit=lambda: GALLERY_LIMIT)


@router.get("")
def form(request: Request, actor: Actor, api_keys: ApiKeys) -> Response:
    first = gallery.page(actor)
    providers = ProviderChoice.all()
    return render(
        request,
        "generate_form.html",
        providers=providers,
        selected_provider=settings.aigen.default_provider,
        models=[m.model_dump() for m in ModelChoice.all()],
        api_keys=api_keys,
        gallery=[task_card(j) for j in first.jobs],
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(actor),
        live_poll_s=settings.web.gallery_live_poll_s,
        max_batch=MAX_BATCH,
    )


@router.get("/gallery")
def gallery_page(request: Request, actor: Actor, query: GalleryParams) -> Response:
    """The next page of gallery cards as a fragment gallery-scroll.js appends --
    same partial the page's first page renders, so an appended card is
    indistinguishable from a server-rendered one. `hidden`: only the hidden
    cards (hidden-cards.js's "Show hidden")."""
    found = gallery.page(actor, query)
    return render(
        request,
        "generate_gallery_fragment.html",
        gallery=[task_card(j) for j in found.jobs],
        next_offset=found.next_offset,
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request, actor: Actor) -> Response:
    """The newest page of cards, no sentinel: what live-gallery.js polls to
    prepend runs started in other sessions."""
    return render_polled(
        request,
        "generate_gallery_fragment.html",
        gallery=[task_card(j) for j in gallery.page(actor).jobs],
        next_offset=None,
    )
