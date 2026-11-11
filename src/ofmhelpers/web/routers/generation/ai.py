"""/ai: every model of every provider, through one form and one job type. The
routes and RQ's entry point; what they do is features/generate/service.py.

`run_ai` is the feature's; jobs queued before 2026-11-11 name it by this
module's path, so the name stays importable here."""

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
from ofmhelpers.schemas import JobsQueued
from ofmhelpers.schemas.access import Area
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot, OwnerId
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render_polled

# What other modules and the tests read here (same objects as the package's).
__all__ = ["TASK", "describe", "router", "run_ai"]

router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Access(Area.GENERATE)])


async def _form(request: Request) -> FormData:
    """Read here so the route itself stays sync and runs off the event loop
    (it saves uploads and validates)."""
    return await request.form()


@router.get("/fields")
def fields(request: Request, model: Annotated[str, Query()]) -> Response:
    """The picked model's settings and pickers, swapped into the form. With
    an ETag: the browser revalidates and gets a bodiless 304 while the
    model's schema is unchanged (ai-form.js also keeps it for the page)."""
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
