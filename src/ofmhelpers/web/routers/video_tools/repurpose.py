# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import Response
from pydantic import Field, field_validator

from ofmhelpers.config import settings
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.features.repurpose import service
from ofmhelpers.features.repurpose.service import (
    DEFAULT_COPIES,
    FILES_PREFIX,
    PREFIX,
    TASK,
)
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import (
    Access,
    Actor,
    AssetsRoot,
    OwnerId,
    Storage,
)
from ofmhelpers.web.forms import FormModel, VideoFiles, at_most
from ofmhelpers.web.idempotency import Idempotent, IdempotentRoute
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render

__all__ = ["DEFAULT_COPIES", "FILES_PREFIX", "TASK", "router"]

router = APIRouter(
    prefix=PREFIX,
    tags=["spoof"],
    dependencies=[Access(Area.REPURPOSE)],
    route_class=IdempotentRoute,
)


class RepurposeForm(FormModel):
    video: VideoFiles
    video_manifest: str = "[]"
    copies: int = Field(DEFAULT_COPIES, ge=1)

    @field_validator("copies")
    @classmethod
    def _at_most_max_copies(cls, copies: int) -> int:
        return at_most(copies, settings.repurpose.max_copies)


@router.get("")
def form(
    request: Request,
    actor: Actor,
    storage: Storage,
    assets_root: AssetsRoot,
    video: Annotated[list[str] | None, Query()] = None,
) -> Response:
    view = service.page_view(actor, storage.uploads, assets_root, video or [])
    return render(request, "repurpose.html", dict(view))


@router.get("/cards/{job_id}")
def cards(request: Request, job_id: str, actor: Actor, storage: Storage) -> Response:
    return render(
        request,
        "_repurpose_cards.html",
        dict(service.cards_view(job_id, actor, storage.uploads)),
    )


@router.post("/run", status_code=202, dependencies=[Idempotent])
def run(
    form: Annotated[RepurposeForm, Form()],
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> JobsQueued:
    return service.start(form, actor, owner_id, assets_root)


add_job_routes(
    router,
    "Spoof",
    lambda _job: "Spoofing…",
    back_url=PREFIX,
    back_label="spoof another video",
)
