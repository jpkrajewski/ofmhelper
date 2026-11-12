"""
/repurpose: one video in, N repurposed copies out (`ofmhelpers.media.repurpose`),
each scored against its source (`repurpose.checker`). Every knob is on the
page; a ready-made preset fills them; the current knobs download as a preset
JSON; a card's only button, Recreate, puts its video, preset and knobs
back. Both roles; a user only ever sees their own runs.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response
from pydantic import Field, field_validator

from ofmhelpers.config import settings
from ofmhelpers.features.repurpose import service
from ofmhelpers.features.repurpose.service import (
    DEFAULT_COPIES,
    DEFAULT_DEVICE,
    FILES_PREFIX,
    PREFIX,
    TASK,
)

# Jobs queued before 2026-11-11 name the run function by this module's path.
from ofmhelpers.features.repurpose.service import run_repurpose as _run_repurpose
from ofmhelpers.media.repurpose.presets import Preset
from ofmhelpers.schemas import JobQueued
from ofmhelpers.schemas.access import Area
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
    # A string the feature parses: a knob it cannot take is a worded 400, which
    # a validator here would turn into a 422 the page does not show.
    preset: str = "{}"
    preset_name: str = ""
    special: bool = False
    device: str = DEFAULT_DEVICE
    copies: int = Field(DEFAULT_COPIES, ge=1)

    @field_validator("copies")
    @classmethod
    def _at_most_max_copies(cls, copies: int) -> int:
        """The limit is a setting, read when a form is checked."""
        return at_most(copies, settings.web.uniqify_max_copies)


@router.get("")
def form(request: Request, actor: Actor) -> Response:
    return render(request, "repurpose.html", **dict(service.page_view(actor)))


@router.get("/cards/{job_id}")
def cards(request: Request, job_id: str, actor: Actor) -> Response:
    """A finished job's cards as the gallery draws them (repurpose.js swaps
    them in for the ones generation.js built)."""
    return render(
        request, "_repurpose_cards.html", cards=service.cards_of(job_id, actor)
    )


@router.post("/preset.json")
def preset_json(preset: Preset) -> Response:
    """The page's knobs (and device) as a preset file."""
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
