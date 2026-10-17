"""
ofmhelpers/web/routers/generation/index.py

Unified Higgsfield-style page for Seedance 2.0 / Seedance 2.5 / Kling 3.0 /
Wan 3.0 / MiniMax H3 / Nano Banana Pro / Seedream 4.5 / Seedream 5.0 / GPT
Image 2.5 Flare (plus the Fake AI Model testing tool): one prompt+settings form (a
tool picker switches which fieldset is active) posting straight to each tool's
existing /run endpoint, and a non-blocking gallery of the last 20 generations
across all of them that a click reloads back into the form.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from ofmhelpers.aigenproviders.kaiai.types import Seedance2Model
from ofmhelpers.config import settings
from ofmhelpers.web.api_keys import get_kie_api_key
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.registry import GENERATE_TOOLS, task_card
from ofmhelpers.web.schemas import TaskCard
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
    return render(
        request,
        "generate_form.html",
        models=[m.value for m in Seedance2Model],
        kie_api_key=get_kie_api_key(request),
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
