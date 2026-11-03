"""/ai: every model of every provider, through one form and one job type. The
routes and RQ's entry point; what they do is features/generate/service.py.

RQ stores `run_ai` by this module's path, recovery re-runs it through the
registry and /clone and /pictures call it, so it stays here as a thin wrapper."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.datastructures import FormData

from ofmhelpers.config import settings
from ofmhelpers.features.generate import service
from ofmhelpers.features.generate.service import (
    TASK,
    GenerateRefusedError,
    describe,
    pending_message,
    start_runs,
)
from ofmhelpers.integrations.ai_providers.catalog import get_spec
from ofmhelpers.schemas import JobsQueued, ResultFile
from ofmhelpers.web.dependencies import Actor, AssetsRoot, OwnerId
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render_polled

# What other modules and the tests read here (same objects as the package's).
__all__ = ["MAX_BATCH", "TASK", "describe", "router", "run_ai"]

router = APIRouter(prefix="/ai", tags=["ai"])

# The most images one submit may queue (each its own job and gallery card).
MAX_BATCH = settings.aigen.max_batch


async def _form(request: Request) -> FormData:
    """Read here so the route itself stays sync and runs off the event loop
    (it saves uploads and validates)."""
    return await request.form()


@router.get("/fields")
def fields(request: Request, model: Annotated[str, Query()]):
    """The picked model's settings and pickers, swapped into the form. With
    an ETag: the browser revalidates and gets a bodiless 304 while the
    model's schema is unchanged (ai-form.js also keeps it for the page)."""
    try:
        spec = get_spec(model)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown model") from None
    return render_polled(request, "_ai_fields.html", spec=spec)


@router.post("/run")
def run(
    form: Annotated[FormData, Depends(_form)],
    assets_root: AssetsRoot,
    owner_id: OwnerId,
    actor: Actor,
) -> JobsQueued:
    try:
        return start_runs(form, assets_root, owner_id, actor, run_ai)
    except GenerateRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


def run_ai(
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    model: str,
    part: int = 0,
    **values: Any,
) -> list[ResultFile]:
    """`service.run_ai`, under the path RQ stores."""
    return service.run_ai(
        job_id, api_key, assets_root, provider, model, part=part, **values
    )


add_job_routes(
    router,
    "AI generation",
    pending_message,
    back_url="/generate",
    back_label="generate another",
    show_inputs=True,
)
