from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ofmhelpers.config import settings
from ofmhelpers.features.clone_links import service
from ofmhelpers.features.clone_links.job import run_clone_link
from ofmhelpers.features.clone_links.models import (
    CheckedLinks,
    PreviewsRequest,
    RecreateLinks,
)
from ofmhelpers.features.clone_links.page import form_view
from ofmhelpers.features.clone_links.service import (
    BATCH,
    FILES_PREFIX,
    LABEL,
    NOT_QUEUED,
    PREFIX,
    TASK,
    THUMBS_PREFIX,
    BatchStopped,
    Generate,
    Sent,
)
from ofmhelpers.features.clone_pricing.targets import DEFAULT_RESOLUTION
from ofmhelpers.features.jobs.schema import CloneQueued
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import (
    Access,
    Actor,
    ApiKeys,
    AssetsRoot,
    CurrentUser,
    OwnerId,
    Scope,
    Storage,
    Viewing,
)
from ofmhelpers.web.forms import Files, PaidForm
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.responses import PRIVATE_FOR_A_DAY
from ofmhelpers.web.routers.generation.clone import default_prompt
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render, render_partial, render_polled

__all__ = [
    "BATCH",
    "FILES_PREFIX",
    "LABEL",
    "NOT_QUEUED",
    "TASK",
    "THUMBS_PREFIX",
    "router",
    "run_clone_link",
]

router = APIRouter(
    prefix=PREFIX, tags=["clone-links"], dependencies=[Access(Area.CLONE_LINKS)]
)

_UNBUFFERED = {"X-Accel-Buffering": "no"}

gallery = Gallery({TASK})


class CloneLinksForm(PaidForm):
    prompt: str = Field(default_factory=default_prompt, min_length=1)
    resolution: str = DEFAULT_RESOLUTION
    audio: bool = False
    export: str = Field(default_factory=lambda: settings.exports.default)
    mirror: bool = False
    image: Files
    image_manifest: str = "[]"
    links: list[str] = Field(default_factory=list)
    batch: str | None = Field(None, pattern=BATCH)


class ReviewState(BaseModel):
    job_id: str
    settled: bool
    html: str


@router.post("/check")
def check(links: Annotated[str, Form()] = "") -> CheckedLinks:
    return CheckedLinks(links=service.read(links))


@router.post("/previews")
def previews(
    body: PreviewsRequest, user: CurrentUser, owner_id: OwnerId
) -> StreamingResponse:
    found = service.previews(
        body.urls, body.provider, body.resolution, user.id, owner_id
    )
    return StreamingResponse(
        (p.model_dump_json() + "\n" for p in found),
        media_type="application/x-ndjson",
        headers=_UNBUFFERED,
    )


@router.get("/thumb/{key}")
def thumb(key: str, owner_id: OwnerId) -> FileResponse:
    return FileResponse(
        service.cover(key, owner_id), media_type="image/webp", headers=PRIVATE_FOR_A_DAY
    )


def _queued(sent: Sent, user_id: int) -> CloneQueued:
    html = "".join(
        render_partial("_clone_links_card.html", {"job": card})
        for card in service.cards(list(reversed(sent.jobs)), user_id)
    )
    return CloneQueued(job_ids=[j.id for j in sent.jobs], html=html, batch=sent.batch)


@router.post("/run", status_code=202)
def run(
    form: Annotated[CloneLinksForm, Form()],
    user: CurrentUser,
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> CloneQueued:
    generate = Generate(
        actor=actor,
        user_id=user.id,
        owner_id=owner_id,
        assets_root=assets_root,
        run=run_clone_link,
    )
    return _queued(generate.start(form), user.id)


@router.get("")
def form(
    request: Request,
    user: CurrentUser,
    actor: Actor,
    viewer: Viewing,
    scope: Scope,
    api_keys: ApiKeys,
    storage: Storage,
) -> Response:
    first = gallery.page(viewer)
    return render(
        request,
        "clone_links.html",
        {
            "gallery": service.cards(first.jobs, scope),
            "next_offset": first.next_offset,
            "hidden_count": gallery.hidden_count(viewer),
            "api_keys": api_keys,
            **dict(form_view(actor, user.id, storage.space)),
        },
    )


@router.get("/gallery")
def gallery_page(
    request: Request, scope: Scope, viewer: Viewing, query: GalleryParams
) -> Response:
    found = gallery.page(viewer, query)
    return render(
        request,
        "clone_links_gallery_fragment.html",
        {
            "gallery": service.cards(found.jobs, scope),
            "next_offset": found.next_offset,
        },
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request, viewer: Viewing, scope: Scope) -> Response:
    return render_polled(
        request,
        "clone_links_gallery_fragment.html",
        {
            "gallery": service.cards(gallery.page(viewer).jobs, scope),
            "next_offset": None,
        },
    )


@router.get("/recreate/{job_id}")
def recreate_links(job_id: str, actor: Actor) -> RecreateLinks:
    return service.recreate(job_id, actor)


@router.post("/stop/{job_id}")
def stop(job_id: str, actor: Actor) -> BatchStopped:
    return service.stop(job_id, actor)


@router.get("/reviews")
def reviews(
    job: Annotated[list[str], Query(max_length=200)], actor: Actor, scope: Scope
) -> list[ReviewState]:
    return [
        ReviewState(
            job_id=card.job_id,
            settled=card.review_settled,
            html=render_partial("_clone_from_link.html", {"job": card}),
        )
        for card in service.cards_of(job, actor, scope)
    ]


add_job_routes(
    router,
    LABEL,
    lambda _job: "Generating video… this can take a few minutes.",
    back_url="/clone-links",
    back_label="clone more",
    show_inputs=True,
)
