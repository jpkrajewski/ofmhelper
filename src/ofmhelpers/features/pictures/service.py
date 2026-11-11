"""What /pictures does behind its routes
(web/routers/image_tools/pictures.py): presets -- the owner's own, the agency's
shared with everyone (`stores.picture_presets`), their images saved in the
owner's part of the asset store --, what the page's form shows, and a submit:
one job per picture to recreate, each checked against the model before
anything is queued."""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel
lazy from fastapi import UploadFile

from ofmhelpers.config import settings
from ofmhelpers.db.stores import picture_presets
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.manifests import build_ordered_paths
from ofmhelpers.features.assets.store import save_asset
from ofmhelpers.features.generate.choices import provider_choices
from ofmhelpers.features.generate.providers import start_provider
from ofmhelpers.features.generate.service import run_ai
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.pictures.targets import (
    PICTURE_TARGETS,
    PRESET_ASPECTS,
    PictureTarget,
    resolution,
    run_aspect,
    values,
)
from ofmhelpers.features.previews.render import PREVIEW_ERRORS, file_thumb
from ofmhelpers.features.refusals import ForbiddenError, NotFoundError, RefusedError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import JobsQueued, PicturePreset, ProviderChoice, ResultFile

logger = get_logger(__name__)

TASK = "pictures"
LABEL = "Pictures"


# --- the page ------------------------------------------------------------------


class FormView(BaseModel):
    """The form's part of the page; each field is a name the template reads."""

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
    """`preset`: the one the page opens on, when the asker may see it."""
    presets = picture_presets.visible(owner_id)
    ids = [p.id for p in presets]
    return FormView(
        presets=presets,
        selected=preset if preset in ids else (ids[0] if ids else None),
        providers=provider_choices(list(PICTURE_TARGETS)),
        selected_provider=start_provider(
            actor, frozenset({TASK}), list(PICTURE_TARGETS)
        ),
        max_images={name: t.max_images for name, t in PICTURE_TARGETS.items()},
        preset_max_images=settings.pictures.preset_max_images,
        aspects=PRESET_ASPECTS,
        default_aspect=settings.pictures.aspect_ratio,
        max_pictures=settings.pictures.max_pictures,
        live_poll_s=settings.web.gallery_live_poll_s,
    )


# --- presets -------------------------------------------------------------------


class PresetFields(Protocol):
    """A preset form as its route read it (`PresetForm`)."""

    name: str
    prompt: str
    aspect_ratio: str
    images: list[UploadFile]


def visible(preset_id: int, owner_id: int) -> PicturePreset:
    found = picture_presets.get(preset_id, owner_id)
    if found is None:
        msg = "No such preset"
        raise NotFoundError(msg)
    return found


def image_thumb(preset_id: int, index: int, owner_id: int) -> Path:
    """A preset image as a small webp (made once and kept): a reference can
    be a 25 MB PNG."""
    preset = visible(preset_id, owner_id)
    if not 0 <= index < len(preset.images) or not Path(preset.images[index]).is_file():
        msg = "No such image"
        raise NotFoundError(msg)
    try:
        return file_thumb(Path(preset.images[index]))
    except PREVIEW_ERRORS:
        logger.warning(
            "pictures: no thumbnail of %s", preset.images[index], exc_info=True
        )
        msg = "No thumbnail"
        raise NotFoundError(msg) from None


def _preset_aspect(value: str) -> str | None:
    """A form's shape: "" = the picture's own (None)."""
    if not value:
        return None
    if value not in PRESET_ASPECTS:
        msg = "Unknown picture shape"
        raise RefusedError(msg)
    return value


def _saved_images(uploads: list[UploadFile], root: Path) -> list[str]:
    """New preset images into the owner's asset store (deduped by
    content); anything but an image is refused."""
    paths = []
    for upload in uploads:
        if not upload.filename:
            continue
        saved = save_asset(upload, root)
        if classify_kind(saved) != "image":
            msg = "A preset takes images only"
            raise RefusedError(msg)
        paths.append(saved)
    return paths


def _checked(images: list[str]) -> list[str]:
    most = settings.pictures.preset_max_images
    if not 1 <= len(images) <= most:
        msg = f"A preset has 1 to {most} pictures"
        raise RefusedError(msg)
    return images


def create_preset(
    form: PresetFields, owner_id: int, assets_root: Path
) -> PicturePreset:
    aspect = _preset_aspect(form.aspect_ratio)
    paths = _checked(_saved_images(form.images, assets_root))
    return picture_presets.create(owner_id, form.name, form.prompt, paths, aspect)


def update_preset(
    preset_id: int,
    form: PresetFields,
    keep: list[int],
    owner_id: int,
    assets_root: Path,
) -> PicturePreset:
    """`keep`: the indexes of the current images to keep, in order; new
    images go after them."""
    preset = visible(preset_id, owner_id)
    if not preset.mine:
        msg = "Only its owner edits a preset"
        raise ForbiddenError(msg)
    kept = [preset.images[i] for i in keep if 0 <= i < len(preset.images)]
    paths = _checked(kept + _saved_images(form.images, assets_root))
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
    """The deleted preset's id."""
    if not picture_presets.delete(preset_id, owner_id):
        msg = "No such preset of yours"
        raise NotFoundError(msg)
    return preset_id


# --- runs ------------------------------------------------------------------------


class Submitted(Protocol):
    """The /pictures run form as its route read it (`PictureRunForm`)."""

    provider: str
    api_key: str
    preset: int
    pictures: list[UploadFile]
    pictures_manifest: str


def _target(provider: str, api_key: str) -> PictureTarget:
    target = PICTURE_TARGETS.get(provider)
    if target is None:
        msg = "That provider has no Nano Banana Pro"
        raise RefusedError(msg)
    if not api_key:
        msg = "API key is required"
        raise RefusedError(msg)
    return target


def _pictures(form: Submitted, owner_id: int, assets_root: Path) -> list[str]:
    paths = build_ordered_paths(
        form.pictures_manifest, form.pictures, assets_root, user_id=owner_id
    )
    most = settings.pictures.max_pictures
    if not 1 <= len(paths) <= most:
        msg = f"Add 1 to {most} pictures to recreate"
        raise RefusedError(msg)
    if any(classify_kind(p) != "image" for p in paths):
        msg = "Pictures only"
        raise RefusedError(msg)
    return paths


def _runs(
    chosen: PicturePreset, target: PictureTarget, paths: list[str]
) -> list[dict[str, Any]]:
    """Each picture's run, provider-neutral, checked against the model."""
    if len(chosen.images) + 1 > target.max_images:
        msg = (
            f"{chosen.name} has {len(chosen.images)} pictures; Nano Banana Pro"
            f" on this provider takes {target.max_images - 1} with the new one"
        )
        raise RefusedError(msg)
    spec = target.spec
    runs = [
        {
            "prompt": chosen.prompt,
            "preset": chosen.id,
            "preset_name": chosen.name,
            "pictures": [path],
            "reference_images": [*chosen.images, path],
            "aspect_ratio": run_aspect(
                chosen.aspect_ratio or settings.pictures.aspect_ratio or None,
                path,
                spec,
                target.aspect,
            ),
            "resolution": resolution(spec, target.resolution),
        }
        for path in paths
    ]
    for params in runs:
        spec.check(values(target, params).model_dump())
    return runs


def start(
    form: Submitted,
    run: Callable[..., Any],
    actor: str,
    owner_id: int,
    assets_root: Path,
) -> JobsQueued:
    """One job per picture to recreate, each checked against the model
    before anything is queued. `run`: the worker function the router owns
    (RQ stores it by path). The key goes to the worker only."""
    chosen = visible(form.preset, owner_id)
    target = _target(form.provider, form.api_key)
    runs = _runs(chosen, target, _pictures(form, owner_id, assets_root))
    job_ids = []
    for params in runs:
        job_params = {"provider": form.provider, "model": target.model, **params}
        kwargs = {
            "api_key": form.api_key,
            "assets_root": str(assets_root),
            **job_params,
        }
        job_ids.append(start_job(TASK, job_params, run, kwargs, actor=actor))
    return JobsQueued(job_ids=job_ids)


def describe_preset(params: dict[str, Any]) -> str:
    """A card's meta line: the preset it ran."""
    name = params.get("preset_name")
    return f"{LABEL} · {name}" if name else LABEL


def run_picture(
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    model: str,
    **params: Any,
) -> list[ResultFile]:
    """The worker side (and recovery's re-run): the job's provider-neutral
    params onto its target's fields, then `run_ai`."""
    fields = values(PICTURE_TARGETS[provider], params).model_dump()
    return run_ai(
        job_id, api_key, assets_root, provider=provider, model=model, **fields
    )
