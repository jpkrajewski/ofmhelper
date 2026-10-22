"""/ai: every model of every provider, through one form and one job type.

The model picked on /generate is a catalog key (`<provider>:<model id>`);
its input schema decides the settings the form shows (`GET /ai/fields`) and
what `POST /ai/run` accepts. A job stores `provider`, `model` and the inputs
under the schema's own field names -- reference files as lists of asset-store
paths, which /generate's Recreate restores into the pickers -- and the worker
runs it through `aigenproviders.runner`. Nothing here names a provider.
"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.datastructures import FormData, UploadFile

from ofmhelpers.aigenproviders import runner
from ofmhelpers.aigenproviders.base import get_provider, provider_class
from ofmhelpers.aigenproviders.catalog import catalog, get_spec
from ofmhelpers.aigenproviders.errors import InvalidInputError
from ofmhelpers.aigenproviders.spec import ModelSpec
from ofmhelpers.cache import enqueue
from ofmhelpers.log import get_logger
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    build_ordered_paths,
    register_generated_asset,
)
from ofmhelpers.web.schemas import Job, JobPreview, JobQueued, ResultFile
from ofmhelpers.web.stores.jobs import create_job, run_job, set_job_preview
from ofmhelpers.web.templates_config import render_polled

logger = get_logger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])

TASK = "ai"

_PENDING_MESSAGE = {
    "image": "Generating image…",
    "video": "Generating video… this can take a few minutes.",
}


class AiRunForm(BaseModel):
    """The fields every /ai/run carries, whatever the model."""

    provider: str
    model: str
    api_key: str = ""


async def _form(request: Request) -> FormData:
    """Read here so the route itself stays sync and runs off the event loop
    (it saves uploads and validates)."""
    return await request.form()


def _spec_for(form: AiRunForm) -> ModelSpec:
    spec = catalog().get(form.model)
    if spec is None or spec.provider != form.provider:
        raise HTTPException(status_code=400, detail="Unknown model for that provider")
    return spec


def _settings(form: FormData, spec: ModelSpec) -> dict[str, Any]:
    """The scalar settings, typed. A checkbox posts a hidden "false" before
    its own "true", so the last value is the one that counts."""
    raw = {
        f.name: str(values[-1])
        for f in spec.form_fields
        if (values := form.getlist(f.name))
    }
    return spec.parse_form(raw)


def _media(
    form: FormData, spec: ModelSpec, assets_root: Path, user_id: int
) -> dict[str, list[str]]:
    """Each media picker resolved to asset-store paths (always a list, so a
    job's params restore into the same picker). Starlette's `UploadFile`:
    a raw form holds those, not FastAPI's subclass."""
    media = {}
    for field in spec.media_fields:
        files = [v for v in form.getlist(field.name) if isinstance(v, UploadFile)]
        manifest = str(form.get(f"{field.name}_manifest") or "[]")
        paths = build_ordered_paths(manifest, files, assets_root, user_id=user_id)
        if paths:
            media[field.name] = paths
    return media


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
def run(request: Request, form: Annotated[FormData, Depends(_form)]) -> JobQueued:
    head = AiRunForm.model_validate(
        {k: form.get(k) for k in AiRunForm.model_fields if form.get(k) is not None}
    )
    spec = _spec_for(head)
    if provider_class(spec.provider).needs_key and not head.api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")
    assets_root = assets_root_for(request)
    try:
        values = {
            **_settings(form, spec),
            **_media(form, spec, assets_root, AuthMiddleware.owner_id(request)),
        }
        spec.check(values)
    except InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    params = {"provider": spec.provider, "model": spec.key, **values}
    job_id = create_job(TASK, params, actor=AuthMiddleware.actor(request))
    enqueue(
        run_job,
        job_id,
        run_ai,
        {
            "job_id": job_id,
            "api_key": head.api_key.strip(),
            "assets_root": str(assets_root),
            **params,
        },
    )
    return JobQueued(job_id=job_id)


def _hosted(urls: list[str]) -> list[str]:
    """Result URLs a browser can open (the fake provider's are not)."""
    return [u for u in urls if u.startswith(("https://", "http://"))]


def run_ai(
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    model: str,
    **values: Any,
) -> list[ResultFile]:
    """The worker side of a job: upload its references, generate, register
    the results in the requester's part of the asset store. Also what
    recovery re-runs a job with, and /clone runs each video through.

    The hosted result is the job's preview the moment it exists; if only the
    local download then fails, the job still succeeds on the hosted copy
    instead of losing a finished (paid) generation."""
    spec = get_spec(model)
    client = get_provider(provider, api_key)
    payload = spec.request(values, client.upload)
    remote: list[str] = []

    def on_result_urls(urls: list[str]) -> None:
        remote.extend(_hosted(urls))
        if remote:
            set_job_preview(job_id, JobPreview(remote_url=remote[0], kind=spec.kind))

    try:
        paths = runner.generate(client, spec, payload, job_id, on_result_urls)
    except Exception:
        if not remote:
            raise
        logger.warning("local download failed, serving %s only", remote, exc_info=True)
        return [
            ResultFile(
                name=url.rsplit("/", 1)[-1].split("?")[0] or spec.kind,
                path=None,
                remote_url=url,
            )
            for url in remote
        ]
    for path in paths:
        register_generated_asset(path, Path(assets_root))
    return [
        ResultFile(
            name=path.name,
            path=str(path),
            remote_url=remote[i] if i < len(remote) else None,
        )
        for i, path in enumerate(paths)
    ]


def describe(job: Job) -> str:
    """A job's card label: the model, and the provider it ran on."""
    spec = catalog().get(str(job.params.get("model")))
    if spec is None:
        return "AI generation"
    return f"{spec.label} · {provider_class(spec.provider).label}"


def _pending_message(job: Job) -> str:
    spec = catalog().get(str(job.params.get("model")))
    return _PENDING_MESSAGE[spec.kind if spec else "image"]


add_job_routes(
    router,
    "AI generation",
    _pending_message,
    back_url="/generate",
    back_label="generate another",
    show_inputs=True,
)
