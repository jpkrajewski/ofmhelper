# Copyright (C) 2026 jpkrajewski

from pathlib import Path
from typing import Any
lazy from collections.abc import Callable

from pydantic import BaseModel, JsonValue, SkipValidation, ValidationError
from starlette.datastructures import FormData

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.jobs.schema import Job, JobPreview, ResultFile
from ofmhelpers.db.jobs.store import create_job, set_job_preview
from ofmhelpers.features.account.keys import Keys
from ofmhelpers.features.assets.manifests import build_ordered_paths
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.features.refusals import NotFoundError, input_refused
from ofmhelpers.integrations.ai_providers import runner
from ofmhelpers.integrations.ai_providers.base import (
    get_provider,
    provider_class,
    uploader,
)
from ofmhelpers.integrations.ai_providers.catalog import catalog, get_spec
from ofmhelpers.integrations.ai_providers.errors import InvalidInputError
from ofmhelpers.log import get_logger
lazy from ofmhelpers.features.assets.models import MediaUpload
lazy from ofmhelpers.integrations.ai_providers.spec import Fields, ModelSpec

logger = get_logger(__name__)

TASK = "ai"

_PENDING_MESSAGE = {
    "image": "Generating image…",
    "video": "Generating video… this can take a few minutes.",
}


class GenerateRefusedError(Exception): ...


class AiRunForm(BaseModel):
    provider: str
    model: str
    api_key: str = ""
    batch: int = 1


class AiInput(BaseModel, arbitrary_types_allowed=True):
    data: SkipValidation[FormData]
    uploads: dict[str, SkipValidation[list[MediaUpload]]]


class PreparedRun(BaseModel):
    params: dict[str, Any]
    api_key: str
    count: int


def _head(form: FormData) -> AiRunForm:
    try:
        return AiRunForm.model_validate(
            {k: form.get(k) for k in AiRunForm.model_fields if form.get(k) is not None},
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


def _settings(form: FormData, spec: ModelSpec) -> Fields:
    raw = {
        f.name: str(values[-1])
        for f in spec.form_fields
        if (values := form.getlist(f.name))
    }
    with input_refused():
        return spec.parse_form(raw)


class _Media(BaseModel, frozen=True):
    by_field: dict[str, list[str]]


def _media(form: AiInput, spec: ModelSpec, assets_root: Path, user_id: int) -> _Media:
    media = {}
    for field in spec.media_fields:
        files = form.uploads.get(field.name, [])
        manifest = str(form.data.get(f"{field.name}_manifest") or "[]")
        paths = build_ordered_paths(manifest, files, assets_root, user_id=user_id)
        if paths:
            media[field.name] = paths
    return _Media(by_field=media)


def model_spec(model: str) -> ModelSpec:
    try:
        return get_spec(model)
    except KeyError:
        msg = "Unknown model"
        raise NotFoundError(msg) from None


def prepare_run(form: AiInput, assets_root: Path, user_id: int) -> PreparedRun:
    head = _head(form.data)
    spec = _spec_for(head)
    api_key = Keys.of(user_id).for_run(spec.provider, spec.key, head.api_key)
    if provider_class(spec.provider).needs_key and not api_key:
        msg = "API key is required"
        raise GenerateRefusedError(msg)
    try:
        values = {
            **_settings(form.data, spec).model_dump(),
            **_media(form, spec, assets_root, user_id).by_field,
        }
        spec.check(values)
    except InvalidInputError as exc:
        raise GenerateRefusedError(str(exc)) from None
    largest = settings.aigen.max_batch
    return PreparedRun(
        params={"provider": spec.provider, "model": spec.key, **values},
        api_key=api_key,
        count=max(1, min(head.batch, largest)) if spec.kind == "image" else 1,
    )


def start_runs(
    form: AiInput,
    assets_root: Path,
    user_id: int,
    actor: str,
    worker: Callable[..., list[ResultFile]],
) -> JobsQueued:
    run = prepare_run(form, assets_root, user_id)
    job_ids: list[str] = []
    for _ in range(run.count):
        job_id = create_job(TASK, run.params, actor=actor)
        enqueue(
            run_job,
            (
                job_id,
                worker,
                {
                    "job_id": job_id,
                    "api_key": run.api_key,
                    "assets_root": str(assets_root),
                    **run.params,
                },
            ),
        )
        job_ids.append(job_id)
    return JobsQueued(job_ids=job_ids)


def _hosted(urls: list[str]) -> list[str]:
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
    paths: list[Path],
    remote: list[str],
    assets_root: Path,
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


def run_ai(  # noqa: PLR0913, PLR0917
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    model: str,
    part: int = 0,
    **values: JsonValue,
) -> list[ResultFile]:
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
    spec = catalog().get(str(job.params.get("model")))
    if spec is None:
        return "AI generation"
    return f"{spec.label} · {provider_class(spec.provider).label}"


def pending_message(job: Job) -> str:
    spec = catalog().get(str(job.params.get("model")))
    return _PENDING_MESSAGE[spec.kind if spec else "image"]
