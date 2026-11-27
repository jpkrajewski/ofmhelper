# Copyright (C) 2026 jpkrajewski

import logging
from pathlib import Path
from typing import Any, Protocol
lazy from collections.abc import Callable

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.picture_presets import store as picture_presets
from ofmhelpers.db.picture_presets.schema import PicturePreset
from ofmhelpers.features.account.keys import Keys
from ofmhelpers.features.assets.manifests import build_ordered_paths
from ofmhelpers.features.assets.store import save_asset
from ofmhelpers.features.generate.choices import provider_choices
from ofmhelpers.features.generate.providers import start_provider
from ofmhelpers.features.generate.service import run_ai
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.features.pictures.targets import (
    PICTURE_TARGETS,
    PRESET_ASPECTS,
    PictureTarget,
    resolution,
    run_aspect,
    values,
)
from ofmhelpers.features.previews.render import PREVIEW_ERRORS, file_thumb
from ofmhelpers.features.refusals import (
    ForbiddenError,
    NotFoundError,
    RefusedError,
    duplicate_refused,
    input_refused,
)
from ofmhelpers.integrations.ai_providers.schema import ProviderChoice
lazy from ofmhelpers.db.jobs.schema import ResultFile
lazy from ofmhelpers.features.assets.models import ImageUpload

logger = logging.getLogger(__name__)

TASK = "pictures"
LABEL = "Pictures"


class FormView(BaseModel):
    presets: list[PicturePreset]
    selected: int | None
    providers: list[ProviderChoice]
    selected_provider: str
    max_images: dict[str, int]
    preset_max_images: int
    aspects: tuple[str, ...]
    default_aspect: str
    max_pictures: int
    live_poll_s: int


def form_view(owner_id: int, actor: str, preset: int | None) -> FormView:
    presets = picture_presets.visible(owner_id)
    ids = [p.id for p in presets]
    return FormView(
        presets=presets,
        selected=preset if preset in ids else (ids[0] if ids else None),
        providers=provider_choices(list(PICTURE_TARGETS), notices=False),
        selected_provider=start_provider(
            actor,
            frozenset({TASK}),
            list(PICTURE_TARGETS),
        ),
        max_images={name: t.max_images for name, t in PICTURE_TARGETS.items()},
        preset_max_images=settings.pictures.preset_max_images,
        aspects=PRESET_ASPECTS,
        default_aspect=settings.pictures.aspect_ratio,
        max_pictures=settings.pictures.max_pictures,
        live_poll_s=settings.web.gallery_live_poll_s,
    )


class PresetFields(Protocol):
    name: str
    prompt: str
    aspect_ratio: str
    images: list[ImageUpload]


def visible(preset_id: int, owner_id: int) -> PicturePreset:
    found = picture_presets.get(preset_id, owner_id)
    if found is None:
        msg = "No such preset"
        raise NotFoundError(msg)
    return found


def image_thumb(preset_id: int, index: int, owner_id: int) -> Path:
    preset = visible(preset_id, owner_id)
    if not 0 <= index < len(preset.images) or not Path(preset.images[index]).is_file():
        msg = "No such image"
        raise NotFoundError(msg)
    try:
        return file_thumb(Path(preset.images[index]))
    except PREVIEW_ERRORS:
        logger.warning(
            "pictures: no thumbnail of %s",
            preset.images[index],
            exc_info=True,
        )
        msg = "No thumbnail"
        raise NotFoundError(msg) from None


def _preset_aspect(value: str) -> str | None:
    if not value:
        return None
    if value not in PRESET_ASPECTS:
        msg = "Unknown picture shape"
        raise RefusedError(msg)
    return value


def _saved_images(uploads: list[ImageUpload], root: Path) -> list[str]:
    return [save_asset(upload, root) for upload in uploads]


def _checked(images: list[str]) -> list[str]:
    most = settings.pictures.preset_max_images
    if not 1 <= len(images) <= most:
        msg = f"A preset has 1 to {most} pictures"
        raise RefusedError(msg)
    return images


def create_preset(
    form: PresetFields,
    owner_id: int,
    assets_root: Path,
) -> PicturePreset:
    aspect = _preset_aspect(form.aspect_ratio)
    paths = _checked(_saved_images(form.images, assets_root))
    with duplicate_refused():
        return picture_presets.create(owner_id, form.name, form.prompt, paths, aspect)


def update_preset(
    preset_id: int,
    form: PresetFields,
    keep: list[int],
    owner_id: int,
    assets_root: Path,
) -> PicturePreset:
    preset = visible(preset_id, owner_id)
    if not preset.mine:
        msg = "Only its owner edits a preset"
        raise ForbiddenError(msg)
    kept = [preset.images[i] for i in keep if 0 <= i < len(preset.images)]
    paths = _checked(kept + _saved_images(form.images, assets_root))
    with duplicate_refused():
        saved = picture_presets.update(
            preset_id,
            preset.owner_id,
            form.name,
            form.prompt,
            paths,
            _preset_aspect(form.aspect_ratio),
        )
    if saved is None:
        msg = "No such preset"
        raise NotFoundError(msg)
    return saved


def delete_preset(preset_id: int, owner_id: int) -> int:
    if not picture_presets.delete(preset_id, owner_id):
        msg = "No such preset of yours"
        raise NotFoundError(msg)
    return preset_id


class Submitted(Protocol):
    provider: str
    api_key: str
    preset: int
    pictures: list[ImageUpload]
    pictures_manifest: str


def _target(provider: str) -> PictureTarget:
    target = PICTURE_TARGETS.get(provider)
    if target is None:
        msg = "That provider has no Nano Banana Pro"
        raise RefusedError(msg)
    return target


def _key(form: Submitted, target: PictureTarget, owner_id: int) -> str:
    api_key = Keys.of(owner_id).for_run(form.provider, target.model, form.api_key)
    if not api_key:
        msg = "API key is required"
        raise RefusedError(msg)
    return api_key


def _pictures(form: Submitted, owner_id: int, assets_root: Path) -> list[str]:
    paths = build_ordered_paths(
        form.pictures_manifest,
        form.pictures,
        assets_root,
        user_id=owner_id,
    )
    most = settings.pictures.max_pictures
    if not 1 <= len(paths) <= most:
        msg = f"Add 1 to {most} pictures to recreate"
        raise RefusedError(msg)
    return paths


class PictureRun(BaseModel):
    prompt: str
    preset: int
    preset_name: str
    pictures: list[str]
    reference_images: list[str]
    aspect_ratio: str
    resolution: str


def _runs(
    chosen: PicturePreset,
    target: PictureTarget,
    paths: list[str],
) -> list[PictureRun]:
    if len(chosen.images) + 1 > target.max_images:
        msg = (
            f"{chosen.name} has {len(chosen.images)} pictures; Nano Banana Pro"
            f" on this provider takes {target.max_images - 1} with the new one"
        )
        raise RefusedError(msg)
    spec = target.spec
    runs = [
        PictureRun(
            prompt=chosen.prompt,
            preset=chosen.id,
            preset_name=chosen.name,
            pictures=[path],
            reference_images=[*chosen.images, path],
            aspect_ratio=run_aspect(
                chosen.aspect_ratio or settings.pictures.aspect_ratio or None,
                path,
                spec,
                target.aspect,
            ),
            resolution=resolution(spec, target.resolution),
        )
        for path in paths
    ]
    with input_refused():
        for run in runs:
            spec.check(values(target, run.model_dump()).model_dump())
    return runs


def start(
    form: Submitted,
    run: Callable[..., Any],
    actor: str,
    owner_id: int,
    assets_root: Path,
) -> JobsQueued:
    chosen = visible(form.preset, owner_id)
    target = _target(form.provider)
    api_key = _key(form, target, owner_id)
    runs = _runs(chosen, target, _pictures(form, owner_id, assets_root))
    job_ids = []
    for picture_run in runs:
        job_params = {
            "provider": form.provider,
            "model": target.model,
            **picture_run.model_dump(),
        }
        kwargs = {
            "api_key": api_key,
            "assets_root": str(assets_root),
            **job_params,
        }
        job_ids.append(start_job(TASK, job_params, run, kwargs, actor=actor))
    return JobsQueued(job_ids=job_ids)


def describe_preset(params: dict[str, Any]) -> str:
    name = params.get("preset_name")
    return f"{LABEL} · {name}" if name else LABEL


def run_picture(  # noqa: PLR0913, PLR0917
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    model: str,
    prompt: str,
    reference_images: list[str],
    aspect_ratio: str,
    resolution: str,
    preset: int | None = None,  # noqa: ARG001
    preset_name: str | None = None,  # noqa: ARG001
    pictures: list[str] | None = None,  # noqa: ARG001
) -> list[ResultFile]:
    chosen = {
        "prompt": prompt,
        "reference_images": reference_images,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }
    fields = values(PICTURE_TARGETS[provider], chosen).model_dump()
    return run_ai(
        job_id,
        api_key,
        assets_root,
        provider=provider,
        model=model,
        **fields,
    )
