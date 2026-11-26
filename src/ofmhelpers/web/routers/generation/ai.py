# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile

from ofmhelpers.features.assets.models import (
    MediaUpload,
    validate_audio_upload,
    validate_image_upload,
    validate_video_upload,
)
from ofmhelpers.features.generate import service
from ofmhelpers.features.generate.service import (
    TASK,
    describe,
    pending_message,
    run_ai,
    start_runs,
)
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.features.kinds import FileKind
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot, OwnerId
from ofmhelpers.web.idempotency import Idempotent, IdempotentRoute
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render_polled

__all__ = ["TASK", "describe", "router", "run_ai"]

router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    dependencies=[Access(Area.GENERATE)],
    route_class=IdempotentRoute,
)


async def _form(request: Request) -> service.AiInput:
    data = await request.form()
    try:
        spec = service.model_spec(str(data.get("model") or ""))
    except NotFoundError:  # silent: prepare_run returns the unknown-model refusal
        return service.AiInput(data=data, uploads={})
    validators = {
        FileKind.IMAGE: validate_image_upload,
        FileKind.VIDEO: validate_video_upload,
        FileKind.AUDIO: validate_audio_upload,
    }
    uploads: dict[str, list[MediaUpload]] = {}
    for field in spec.media_fields:
        uploads[field.name] = [
            validators[FileKind(field.kind)](value)
            for value in data.getlist(field.name)
            if isinstance(value, UploadFile)
        ]
    return service.AiInput(data=data, uploads=uploads)


@router.get("/fields")
def fields(request: Request, model: Annotated[str, Query()]) -> Response:
    return render_polled(
        request,
        "_ai_fields.html",
        {"spec": service.model_spec(model)},
    )


@router.post("/run", status_code=202, dependencies=[Idempotent])
def run(
    form: Annotated[service.AiInput, Depends(_form)],
    assets_root: AssetsRoot,
    owner_id: OwnerId,
    actor: Actor,
) -> JobsQueued:
    return start_runs(form, assets_root, owner_id, actor, run_ai)


add_job_routes(
    router,
    "AI generation",
    pending_message,
    back_url="/generate",
    back_label="generate another",
    show_inputs=True,
)
