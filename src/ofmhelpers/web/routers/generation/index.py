"""
/generate: one form for every model of every provider -- provider, then
model, then the model's own settings (loaded from /ai/fields) -- posting to
/ai/run, and a non-blocking gallery of past generations that a click reloads
back into the form.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from ofmhelpers.config import settings
from ofmhelpers.web.api_keys import get_api_keys
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.registry import GENERATE_TOOLS, task_card
from ofmhelpers.web.schemas import ModelChoice, ProviderChoice, TaskCard
from ofmhelpers.web.stores.jobs import list_jobs_page
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/generate", tags=["generate"])

GALLERY_LIMIT = settings.web.gallery_limit


def _gallery_page(request: Request, offset: int) -> tuple[list[TaskCard], int | None]:
    """One page of gallery cards (only the session's own, for a VA) plus the
    offset of the next page, or None when this was the last one. The absence of
    a next offset is what ends the infinite scroll, so there is no separate
    "has_more" flag to keep in step."""
    page, total = list_jobs_page(
        set(GENERATE_TOOLS),
        offset,
        GALLERY_LIMIT,
        AuthMiddleware.visible_actor(request),
    )
    next_offset = offset + GALLERY_LIMIT
    return (
        [task_card(j) for j in page],
        next_offset if next_offset < total else None,
    )


@router.get("")
def form(request: Request):
    gallery, next_offset = _gallery_page(request, 0)
    providers = ProviderChoice.all()
    return render(
        request,
        "generate_form.html",
        providers=providers,
        selected_provider=providers[0].name,
        models=[m.model_dump() for m in ModelChoice.all()],
        api_keys=get_api_keys(request),
        gallery=gallery,
        next_offset=next_offset,
        live_poll_s=settings.web.gallery_live_poll_s,
    )


@router.get("/gallery")
def gallery_page(request: Request, offset: Annotated[int, Query(ge=0)] = 0):
    """The next page of gallery cards as a fragment gallery-scroll.js appends --
    same partial the page's first page renders, so an appended card is
    indistinguishable from a server-rendered one."""
    gallery, next_offset = _gallery_page(request, offset)
    return render(
        request,
        "generate_gallery_fragment.html",
        gallery=gallery,
        next_offset=next_offset,
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request):
    """The newest page of cards, no sentinel: what live-gallery.js polls to
    prepend runs started in other sessions."""
    gallery, _ = _gallery_page(request, 0)
    return render(
        request, "generate_gallery_fragment.html", gallery=gallery, next_offset=None
    )
