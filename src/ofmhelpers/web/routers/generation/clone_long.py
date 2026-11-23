# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ofmhelpers.features.clone_long import page as clone_long
from ofmhelpers.features.clone_long.page import (
    DEFAULT_RESOLUTION,
    FILES_PREFIX,
    LABEL,
    PREFIX,
    TASK,
    card,
)
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import (
    Access,
    Actor,
    ApiKeys,
    AssetsRoot,
    OwnerId,
    Viewing,
)
from ofmhelpers.web.forms import Files, PaidForm, Stripped, Upper
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.idempotency import Idempotent, IdempotentRoute
from ofmhelpers.web.responses import NO_STORE
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render

__all__ = ["FILES_PREFIX", "LABEL", "TASK", "card", "router"]

router = APIRouter(
    prefix=PREFIX,
    tags=["clone-long"],
    dependencies=[Access(Area.CLONE_LONG)],
    route_class=IdempotentRoute,
)

gallery = Gallery({TASK})


class LongCloneForm(PaidForm):
    prompt: Stripped = Field("", max_length=2000)
    resolution: Upper = DEFAULT_RESOLUTION
    image: Files
    image_manifest: str = "[]"
    video: Files
    video_manifest: str = "[]"


class LongCloneQueued(BaseModel):
    job_id: str


@router.get("")
def page(  # noqa: PLR0913, PLR0917
    request: Request,
    actor: Actor,
    viewer: Viewing,
    api_keys: ApiKeys,
    assets_root: AssetsRoot,
    video: Annotated[list[str] | None, Query()] = None,
) -> Response:
    first = gallery.page(viewer)
    response = render(
        request,
        "clone_long.html",
        {
            "gallery": clone_long.cards(first.jobs),
            "next_offset": first.next_offset,
            "hidden_count": gallery.hidden_count(viewer),
            "api_keys": api_keys,
            **dict(clone_long.form_view(actor, assets_root, video or [])),
        },
    )
    response.headers.update(NO_STORE)
    return response


@router.get("/gallery")
def gallery_page(request: Request, viewer: Viewing, query: GalleryParams) -> Response:
    found = gallery.page(viewer, query)
    return render(
        request,
        "clone_long_gallery_fragment.html",
        {"gallery": clone_long.cards(found.jobs), "next_offset": found.next_offset},
    )


@router.post("/run", status_code=202, dependencies=[Idempotent])
def run(
    form: Annotated[LongCloneForm, Form()],
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> LongCloneQueued:
    return LongCloneQueued(job_id=clone_long.start(form, actor, owner_id, assets_root))


add_job_routes(
    router,
    LABEL,
    lambda job: (
        "Generating video… this can take a few minutes."
        if job.params.get("pieces", 1) == 1
        else f"Generating {job.params['pieces']} pieces… this takes a while."
    ),
    back_url="/clone-long",
    back_label="clone another",
    show_inputs=True,
)
