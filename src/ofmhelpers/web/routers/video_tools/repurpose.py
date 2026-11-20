from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import Response
from pydantic import Field, field_validator

from ofmhelpers.auth.access import Area
from ofmhelpers.config import settings
from ofmhelpers.features.repurpose import service
from ofmhelpers.features.repurpose.service import (
    DEFAULT_COPIES,
    FILES_PREFIX,
    PREFIX,
    TASK,
)
from ofmhelpers.schemas import JobsQueued
from ofmhelpers.web.dependencies import (
    Access,
    Actor,
    AssetsRoot,
    CurrentUser,
    OwnerId,
)
from ofmhelpers.web.forms import Files, FormModel, at_most
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render

__all__ = ["DEFAULT_COPIES", "FILES_PREFIX", "TASK", "router"]

router = APIRouter(prefix=PREFIX, tags=["spoof"], dependencies=[Access(Area.REPURPOSE)])


class RepurposeForm(FormModel):
    video: Files
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
    user: CurrentUser,
    assets_root: AssetsRoot,
    video: Annotated[list[str] | None, Query()] = None,
) -> Response:
    view = service.page_view(actor, user.id, assets_root, video or [])
    return render(request, "repurpose.html", **dict(view))


@router.get("/cards/{job_id}")
def cards(request: Request, job_id: str, actor: Actor, user: CurrentUser) -> Response:
    return render(
        request,
        "_repurpose_cards.html",
        **dict(service.cards_view(job_id, actor, user.id)),
    )


@router.post("/run", status_code=202)
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
