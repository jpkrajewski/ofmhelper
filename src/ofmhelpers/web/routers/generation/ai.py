from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from starlette.datastructures import FormData

from ofmhelpers.features.generate import service
from ofmhelpers.features.generate.service import (
    TASK,
    describe,
    pending_message,
    run_ai,
    start_runs,
)
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot, OwnerId
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render_polled

__all__ = ["TASK", "describe", "router", "run_ai"]

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Access(Area.GENERATE)])


async def _form(request: Request) -> FormData:
    return await request.form()


@router.get("/fields")
def fields(request: Request, model: Annotated[str, Query()]) -> Response:
    return render_polled(request, "_ai_fields.html", spec=service.model_spec(model))


@router.post("/run", status_code=202)
def run(
    form: Annotated[FormData, Depends(_form)],
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
