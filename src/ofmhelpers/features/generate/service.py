"""What /ai does behind its routes (web/routers/generation/ai.py): a submit's
checks and jobs, and the worker side of a job.

The model picked on /generate is a catalog key (`<provider>:<model id>`); its
input schema decides what a submit accepts. A job stores `provider`, `model`
and the inputs under the schema's own field names -- reference files as lists
of asset-store paths, which /generate's Recreate restores into the pickers --
and the worker runs it through `integrations/ai_providers.runner`. Nothing here
names a provider."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError
from starlette.datastructures import FormData, UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job, set_job_preview
from ofmhelpers.features.account.keys import Keys
from ofmhelpers.features.assets.manifests import build_ordered_paths
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.integrations.ai_providers import runner
from ofmhelpers.integrations.ai_providers.base import (
    get_provider,
    provider_class,
    uploader,
)
from ofmhelpers.integrations.ai_providers.catalog import catalog, get_spec
from ofmhelpers.integrations.ai_providers.errors import InvalidInputError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import Job, JobPreview, JobsQueued, ResultFile
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec

logger = get_logger(__name__)

TASK = "ai"

_PENDING_MESSAGE = {
    "image": "Generating image…",
    "video": "Generating video… this can take a few minutes.",
}


class GenerateRefusedError(Exception):
    """A submit that queues nothing; its text is what the page shows."""


class AiRunForm(BaseModel):
    """The fields every /ai/run carries, whatever the model."""

    provider: str
    model: str
    api_key: str = ""
    # Images only: queue this many of the same prompt, each its own job.
    batch: int = 1


class PreparedRun(BaseModel):
    """A checked submit: what each of its `count` jobs stores, and its key,
    which goes to the worker only."""

    params: dict[str, Any]
    api_key: str
    count: int


def _head(form: FormData) -> AiRunForm:
    try:
        return AiRunForm.model_validate(
            {k: form.get(k) for k in AiRunForm.model_fields if form.get(k) is not None}
        )
    except ValidationError:
        msg = "Some settings have a value they cannot take"
        raise GenerateRefusedError(msg) from None


def _spec_for(head: AiRunForm) -> ModelSpec:
    spec = catalog().get(head.model)
    if spec is None or spec.provider != head.provider:
        msg = "Unknown model for that provider"
        raise GenerateRefusedError(msg)
    return spec


def _settings(form: FormData, spec: ModelSpec) -> dict[str, Any]:
    """The scalar settings, typed. A checkbox posts a hidden "false" before
    its own "true", so the last value is the one that counts."""
    raw = {
        f.name: str(values[-1])
        for f in spec.form_fields
        if (values := form.getlist(f.name))
    }
    return spec.parse_form(raw).model_dump()


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


def model_spec(model: str) -> ModelSpec:
    """The catalog entry of a model the form picked."""
    try:
        return get_spec(model)
    except KeyError:
        msg = "Unknown model"
        raise NotFoundError(msg) from None


def prepare_run(form: FormData, assets_root: Path, user_id: int) -> PreparedRun:
    """A submit checked before anything is queued or paid; refused with the
    reason otherwise. Uploads are saved only once the head is good."""
    head = _head(form)
    spec = _spec_for(head)
    api_key = Keys.of(user_id).for_run(spec.provider, spec.key, head.api_key)
    if provider_class(spec.provider).needs_key and not api_key:
        msg = "API key is required"
        raise GenerateRefusedError(msg)
    try:
        values = {
            **_settings(form, spec),
            **_media(form, spec, assets_root, user_id),
        }
        spec.check(values)
    except InvalidInputError as exc:
        raise GenerateRefusedError(str(exc)) from None
    # Batch is images only: a video model always runs once.
    largest = settings.aigen.max_batch
    return PreparedRun(
        params={"provider": spec.provider, "model": spec.key, **values},
        api_key=api_key,
        count=max(1, min(head.batch, largest)) if spec.kind == "image" else 1,
    )


def start_runs(
    form: FormData,
    assets_root: Path,
    user_id: int,
    actor: str,
    worker: Callable[..., list[ResultFile]],
) -> JobsQueued:
    """One job per run of a checked submit. `worker`: the router's `run_ai`,
    the path RQ stores and a queued job is re-run through."""
    run = prepare_run(form, assets_root, user_id)
    job_ids: list[str] = []
    for _ in range(run.count):
        job_id = create_job(TASK, run.params, actor=actor)
        enqueue(
            run_job,
            job_id,
            worker,
            {
                "job_id": job_id,
                "api_key": run.api_key,
                "assets_root": str(assets_root),
                **run.params,
            },
        )
        job_ids.append(job_id)
    return JobsQueued(job_ids=job_ids)


def _hosted(urls: list[str]) -> list[str]:
    """Result URLs a browser can open (the fake provider's are not)."""
    return [u for u in urls if u.startswith(("https://", "http://"))]


def _hosted_results(remote: list[str], spec: ModelSpec) -> list[ResultFile]:
    return [
        ResultFile(
            name=url.rsplit("/", 1)[-1].split("?")[0] or spec.kind,
            path=None,
            remote_url=url,
        )
        for url in remote
    ]


def _saved_results(
    paths: list[Path], remote: list[str], assets_root: Path
) -> list[ResultFile]:
    for path in paths:
        register_generated_asset(path, assets_root)
    return [
        ResultFile(
            name=path.name,
            path=str(path),
            remote_url=remote[i] if i < len(remote) else None,
        )
        for i, path in enumerate(paths)
    ]


def run_ai(
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    model: str,
    part: int = 0,
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
    remote: list[str] = []

    def on_result_urls(urls: list[str]) -> None:
        remote.extend(_hosted(urls))
        if remote:
            set_job_preview(job_id, JobPreview(remote_url=remote[0], kind=spec.kind))

    try:
        paths = runner.generate(
            client,
            spec,
            # A callable: references upload only when a request is really
            # sent, so a resumed job never uploads them again.
            lambda: spec.request(values, uploader(client, spec)).model_dump(),
            job_id,
            on_result_urls,
            part=part,
        )
    except Exception:
        if not remote:
            raise
        logger.warning("local download failed, serving %s only", remote, exc_info=True)
        return _hosted_results(remote, spec)
    return _saved_results(paths, remote, Path(assets_root))


def describe(job: Job) -> str:
    """A job's card label: the model, and the provider it ran on."""
    spec = catalog().get(str(job.params.get("model")))
    if spec is None:
        return "AI generation"
    return f"{spec.label} · {provider_class(spec.provider).label}"


def pending_message(job: Job) -> str:
    spec = catalog().get(str(job.params.get("model")))
    return _PENDING_MESSAGE[spec.kind if spec else "image"]
