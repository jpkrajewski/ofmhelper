from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from pydantic import Field, field_validator

from ofmhelpers.auth.access import Area
from ofmhelpers.config import settings
from ofmhelpers.features.repurpose import service
from ofmhelpers.features.repurpose.service import (
    DEFAULT_COPIES,
    DEFAULT_DEVICE,
    FILES_PREFIX,
    PREFIX,
    TASK,
)
from ofmhelpers.features.repurpose.service import run_repurpose as _run_repurpose
from ofmhelpers.media.repurpose.presets import Preset
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot, OwnerId
from ofmhelpers.web.forms import Files, FormModel, at_most
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render

__all__ = [
    "DEFAULT_COPIES",
    "DEFAULT_DEVICE",
    "FILES_PREFIX",
    "TASK",
    "_run_repurpose",
    "router",
]

router = APIRouter(
    prefix=PREFIX, tags=["repurpose"], dependencies=[Access(Area.REPURPOSE)]
)


class RepurposeForm(FormModel):
    video: Files
    video_manifest: str = "[]"
    preset: str = "{}"
    preset_name: str = ""
    special: bool = False
    device: str = DEFAULT_DEVICE
    copies: int = Field(DEFAULT_COPIES, ge=1)

    @field_validator("copies")
    @classmethod
    def _at_most_max_copies(cls, copies: int) -> int:
        return at_most(copies, settings.web.uniqify_max_copies)


@router.get("")
def form(request: Request, actor: Actor) -> Response:
    return render(request, "repurpose.html", **dict(service.page_view(actor)))


@router.get("/cards/{job_id}")
def cards(request: Request, job_id: str, actor: Actor) -> Response:
    return render(
        request, "_repurpose_cards.html", cards=service.cards_of(job_id, actor)
    )


@router.post("/preset.json")
def preset_json(preset: Preset) -> Response:
    return Response(
        content=preset.model_dump_json(exclude_unset=True, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="preset.json"'},
    )


@router.post("/run", status_code=202)
def run(
    form: Annotated[RepurposeForm, Form()],
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> JobQueued:
    return service.start(form, actor, owner_id, assets_root)


add_job_routes(
    router,
    "Repurpose",
    lambda job: f"Making {job.params['copies']} copies…",
    back_url="/repurpose",
    back_label="repurpose another video",
)
