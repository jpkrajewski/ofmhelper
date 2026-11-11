"""
/uniqify: upload one video, get back N re-encoded variants of it as one zip,
each with its own speed / zoom / colour / grain / mirror / trim jitter
(`utils.uniqify`). Both roles; a VA only ever sees its own runs.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import Field, field_validator

from ofmhelpers.config import settings
from ofmhelpers.features.uniqify import service
from ofmhelpers.features.uniqify.service import run_uniqify as _run_uniqify
from ofmhelpers.media.uniqify import MetadataMode, Strength
from ofmhelpers.schemas import JobQueued
from ofmhelpers.web.dependencies import Actor
from ofmhelpers.web.forms import FormModel, Stripped, at_most
from ofmhelpers.web.routers.task_helpers import add_job_routes, uploads_root
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/uniqify", tags=["uniqify"])


def _upload_root() -> Path:
    return uploads_root() / "uniqify"


TASK = service.TASK

FILES_PREFIX = f"{router.prefix}/files"


class UniqifyForm(FormModel):
    file: UploadFile
    copies: int = Field(5, ge=1)
    strength: Strength = Strength.HARD
    mirror_pct: float = Field(0.0, ge=0, le=100)
    trim_secs: float = Field(0.1, ge=0)
    metadata: MetadataMode = MetadataMode.STRIP
    output_name: Stripped = ""

    # The upper limits are settings, read when a form is checked (a `le=`
    # would keep what the setting said at import).
    @field_validator("copies")
    @classmethod
    def _at_most_max_copies(cls, copies: int) -> int:
        return at_most(copies, settings.web.uniqify_max_copies)

    @field_validator("trim_secs")
    @classmethod
    def _at_most_max_trim(cls, trim_secs: float) -> float:
        return at_most(trim_secs, settings.web.uniqify_max_trim_s)


@router.get("")
def form(request: Request, actor: Actor) -> Response:
    view = service.form_view(actor, FILES_PREFIX)
    return render(request, "uniqify.html", **dict(view))


@router.post("/run", status_code=202)
def run(form: Annotated[UniqifyForm, Form()], actor: Actor) -> JobQueued:
    return service.start(form, actor, _upload_root(), _run_uniqify)


add_job_routes(
    router,
    "Uniqify",
    lambda job: f"Rendering {job.params['copies']} copies…",
    back_url="/uniqify",
    back_label="uniqify another video",
)
