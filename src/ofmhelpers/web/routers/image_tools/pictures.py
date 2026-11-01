"""
/pictures: presets -- a name, a prompt and up to `OFM_PICTURES_PRESET_MAX_IMAGES`
(10) reference images -- run through Nano Banana Pro with each picture to
recreate. A VA's whole input is the preset and the pictures they found; one
job per picture (task `pictures`) sends the preset's images, then that
picture, with the preset's prompt, at the picture's own aspect ratio (the
nearest the model takes) -- or the preset's fixed shape, else
`OFM_PICTURES_ASPECT_RATIO` (3:4; "" = the picture's own) -- and
`OFM_PICTURES_RESOLUTION`.

Runs go through `ai.run_ai` like any /generate job; `PICTURE_TARGETS` names
each provider's Nano Banana Pro and its fields (Higgsfield has none; a
provider joins by one entry). Presets: the owner's own, the agency's shared
with everyone (`stores.picture_presets`); their images are saved in the
owner's part of the asset store and shown through `/pictures/presets/{id}/
images/{n}` to whoever may see the preset.
"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import InvalidInputError
from ofmhelpers.aigenproviders.spec import ModelSpec
from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.web.api_keys import get_api_keys
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.previews import PREVIEW_ERRORS, file_thumb
from ofmhelpers.web.routers.generation.ai import run_ai
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    assets_root_for_user,
    build_ordered_paths,
    classify_kind,
    save_asset,
)
from ofmhelpers.web.schemas import (
    Job,
    JobsQueued,
    PicturePreset,
    ProviderChoice,
    ResultFile,
    TaskCard,
)
from ofmhelpers.web.stores import picture_presets
from ofmhelpers.web.stores.jobs import (
    count_hidden_jobs,
    create_job,
    list_jobs_page,
    run_job,
)
from ofmhelpers.web.templates_config import render, render_polled

logger = get_logger(__name__)

TASK = "pictures"
LABEL = "Pictures"

router = APIRouter(prefix="/pictures", tags=["pictures"])

# Short: an edited preset may put another image under the same index.
_IMAGE_CACHE = {"Cache-Control": "private, max-age=60"}

PresetName = Annotated[str, Form(min_length=1, max_length=80)]
PresetPrompt = Annotated[str, Form(min_length=1, max_length=20000)]
# The shapes a preset may fix every run to ("" in the form: the picture's
# own); a model without one gets its nearest.
PRESET_ASPECTS = ("1:1", "3:4", "4:5", "2:3", "9:16", "4:3", "3:2", "16:9")
PresetAspect = Annotated[str, Form(max_length=16)]


class PictureTarget(BaseModel):
    """One provider's Nano Banana Pro: the catalog model, its fields, and
    what every run sends besides (`fixed`: kie.ai's output format, JPEG by
    user rule 2026-11-01)."""

    model: str
    images: str
    aspect: str
    resolution: str
    fixed: dict[str, Any] = {}

    @property
    def spec(self) -> ModelSpec:
        return get_spec(self.model)

    @property
    def max_images(self) -> int:
        field = self.spec.properties[self.images]
        return field.max_items or settings.pictures.preset_max_images + 1


PICTURE_TARGETS = {
    "kie": PictureTarget(
        model="kie:nano-banana-pro",
        images="image_input",
        aspect="aspect_ratio",
        resolution="resolution",
        fixed={"output_format": "jpg"},
    ),
    "runninghub": PictureTarget(
        model="runninghub:rhart-image-n-pro-official/edit",
        images="imageUrls",
        aspect="aspectRatio",
        resolution="resolution",
    ),
}


def default_provider() -> str:
    """The app's default provider when it has Nano Banana Pro, else the
    first that does."""
    wanted = settings.aigen.default_provider
    return wanted if wanted in PICTURE_TARGETS else next(iter(PICTURE_TARGETS))


def _ratio(value: str) -> float | None:
    width, _, height = value.partition(":")
    try:
        return float(width) / float(height)
    except (ValueError, ZeroDivisionError):
        return None


def _fallback(spec: ModelSpec, field: str) -> str:
    choices = [str(v) for v in spec.properties[field].enum or []]
    return str(spec.properties[field].default or (choices or ["1:1"])[0])


def nearest_aspect(ratio: float, spec: ModelSpec, field: str) -> str:
    """The ratio `spec` takes nearest to `ratio` (width / height)."""
    choices = [str(v) for v in spec.properties[field].enum or []]
    ratios = [(r, v) for v in choices if (r := _ratio(v)) is not None]
    if not ratios:
        return _fallback(spec, field)
    return min(ratios, key=lambda rv: abs(rv[0] - ratio))[1]


def aspect_of(path: str, spec: ModelSpec, field: str) -> str:
    """The ratio `spec` takes nearest to the picture's own (as shown: EXIF
    rotation applied); the field's default when the picture cannot be
    read."""
    try:
        with Image.open(path) as image:
            width, height = ImageOps.exif_transpose(image).size
    except (OSError, UnidentifiedImageError):
        return _fallback(spec, field)
    return (
        nearest_aspect(width / height, spec, field)
        if height
        else _fallback(spec, field)
    )


def run_aspect(fixed: str | None, path: str, spec: ModelSpec, field: str) -> str:
    """A run's shape: the preset's fixed one (as the model spells it, or its
    nearest), else the picture's own."""
    if not fixed:
        return aspect_of(path, spec, field)
    choices = [str(v) for v in spec.properties[field].enum or []]
    if fixed in choices:
        return fixed
    ratio = _ratio(fixed)
    return nearest_aspect(ratio, spec, field) if ratio else _fallback(spec, field)


def _preset_aspect(value: str) -> str | None:
    """A form's shape: "" = the picture's own (None)."""
    if not value:
        return None
    if value not in PRESET_ASPECTS:
        raise HTTPException(status_code=400, detail="Unknown picture shape")
    return value


def _resolution(spec: ModelSpec, field: str) -> str:
    """`OFM_PICTURES_RESOLUTION` in the model's spelling ("2K" / "2k")."""
    wanted = settings.pictures.resolution
    allowed = [str(v) for v in spec.properties[field].enum or []]
    return next((v for v in allowed if v.lower() == wanted.lower()), wanted)


def _values(target: PictureTarget, params: dict[str, Any]) -> dict[str, Any]:
    """A run's inputs in the target model's own field names."""
    return {
        **target.fixed,
        "prompt": params["prompt"],
        target.images: params["reference_images"],
        target.aspect: params["aspect_ratio"],
        target.resolution: params["resolution"],
    }


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
    values = _values(PICTURE_TARGETS[provider], params)
    return run_ai(
        job_id, api_key, assets_root, provider=provider, model=model, **values
    )


# --- the page and its gallery --------------------------------------------------


def _card(job: Job) -> TaskCard:
    from ofmhelpers.web.routers.registry import (
        task_card,
    )

    return task_card(job)


def _gallery_page(
    request: Request, offset: int, hidden: bool = False
) -> tuple[list[TaskCard], int | None]:
    limit = settings.web.gallery_limit
    jobs, total = list_jobs_page(
        {TASK}, offset, limit, AuthMiddleware.actor(request), hidden=hidden
    )
    next_offset = offset + limit
    return [_card(j) for j in jobs], next_offset if next_offset < total else None


@router.get("")
def page(request: Request, preset: Annotated[int | None, Query()] = None):
    viewer = AuthMiddleware.owner_id(request)
    presets = picture_presets.visible(viewer)
    ids = [p.id for p in presets]
    selected = preset if preset in ids else (ids[0] if ids else None)
    gallery, next_offset = _gallery_page(request, 0)
    return render(
        request,
        "pictures.html",
        presets=presets,
        selected=selected,
        providers=ProviderChoice.all(list(PICTURE_TARGETS)),
        selected_provider=default_provider(),
        api_keys=get_api_keys(request),
        max_images={name: t.max_images for name, t in PICTURE_TARGETS.items()},
        preset_max_images=settings.pictures.preset_max_images,
        aspects=PRESET_ASPECTS,
        default_aspect=settings.pictures.aspect_ratio,
        max_pictures=settings.pictures.max_pictures,
        gallery=gallery,
        next_offset=next_offset,
        hidden_count=count_hidden_jobs({TASK}, AuthMiddleware.actor(request)),
        live_poll_s=settings.web.gallery_live_poll_s,
    )


@router.get("/gallery")
def gallery_page(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    hidden: Annotated[bool, Query()] = False,
):
    gallery, next_offset = _gallery_page(request, offset, hidden)
    return render(
        request,
        "generate_gallery_fragment.html",
        gallery=gallery,
        next_offset=next_offset,
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request):
    gallery, _ = _gallery_page(request, 0)
    return render_polled(
        request, "generate_gallery_fragment.html", gallery=gallery, next_offset=None
    )


# --- presets -------------------------------------------------------------------


def _visible(request: Request, preset_id: int) -> PicturePreset:
    found = picture_presets.get(preset_id, AuthMiddleware.owner_id(request))
    if found is None:
        raise HTTPException(status_code=404, detail="No such preset")
    return found


@router.get("/presets/{preset_id}/images/{index}")
def preset_image(request: Request, preset_id: int, index: int) -> FileResponse:
    """A preset image as a small webp (made once and kept): a reference can
    be a 25 MB PNG."""
    preset = _visible(request, preset_id)
    if not 0 <= index < len(preset.images) or not Path(preset.images[index]).is_file():
        raise HTTPException(status_code=404, detail="No such image")
    try:
        thumb = file_thumb(Path(preset.images[index]))
    except PREVIEW_ERRORS:
        logger.warning(
            "pictures: no thumbnail of %s", preset.images[index], exc_info=True
        )
        raise HTTPException(status_code=404, detail="No thumbnail") from None
    return FileResponse(thumb, media_type="image/webp", headers=_IMAGE_CACHE)


def _saved_images(request: Request, uploads: list[UploadFile]) -> list[str]:
    """New preset images into the owner's asset store (deduped by
    content); anything but an image is refused."""
    root = assets_root_for_user(AuthMiddleware.owner_id(request))
    paths = []
    for upload in uploads:
        if not upload.filename:
            continue
        saved = save_asset(upload, root)
        if classify_kind(saved) != "image":
            raise HTTPException(status_code=400, detail="A preset takes images only")
        paths.append(saved)
    return paths


def _checked(images: list[str]) -> list[str]:
    most = settings.pictures.preset_max_images
    if not 1 <= len(images) <= most:
        raise HTTPException(
            status_code=400, detail=f"A preset has 1 to {most} pictures"
        )
    return images


@router.post("/presets")
def create_preset(
    request: Request,
    name: PresetName,
    prompt: PresetPrompt,
    aspect_ratio: PresetAspect = "",
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> PicturePreset:
    aspect = _preset_aspect(aspect_ratio)
    paths = _checked(_saved_images(request, images or []))
    try:
        return picture_presets.create(
            AuthMiddleware.owner_id(request),
            name.strip(),
            prompt.strip(),
            paths,
            aspect,
        )
    except picture_presets.DuplicateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.post("/presets/{preset_id}")
def update_preset(
    request: Request,
    preset_id: int,
    name: PresetName,
    prompt: PresetPrompt,
    aspect_ratio: PresetAspect = "",
    keep: Annotated[list[int] | None, Form()] = None,
    images: Annotated[list[UploadFile] | None, File()] = None,
) -> PicturePreset:
    """`keep`: the indexes of the current images to keep, in order; new
    images go after them."""
    preset = _visible(request, preset_id)
    if not preset.mine:
        raise HTTPException(status_code=403, detail="Only its owner edits a preset")
    kept = [preset.images[i] for i in keep or [] if 0 <= i < len(preset.images)]
    paths = _checked(kept + _saved_images(request, images or []))
    try:
        saved = picture_presets.update(
            preset_id,
            preset.owner_id,
            name.strip(),
            prompt.strip(),
            paths,
            _preset_aspect(aspect_ratio),
        )
    except picture_presets.DuplicateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if saved is None:
        raise HTTPException(status_code=404, detail="No such preset")
    return saved


@router.post("/presets/{preset_id}/delete")
def delete_preset(request: Request, preset_id: int) -> dict[str, int]:
    if not picture_presets.delete(preset_id, AuthMiddleware.owner_id(request)):
        raise HTTPException(status_code=404, detail="No such preset of yours")
    return {"deleted": preset_id}


# --- runs ------------------------------------------------------------------------


@router.post("/run")
def run(
    request: Request,
    preset: Annotated[int, Form()],
    api_key: Annotated[str, Form()] = "",
    provider: Annotated[str, Form()] = "",
    pictures: Annotated[list[UploadFile] | None, File()] = None,
    pictures_manifest: Annotated[str, Form()] = "[]",
) -> JobsQueued:
    """One job per picture to recreate, each checked against the model
    before anything is queued."""
    chosen = _visible(request, preset)
    provider = provider or default_provider()
    target = PICTURE_TARGETS.get(provider)
    if target is None:
        raise HTTPException(
            status_code=400, detail="That provider has no Nano Banana Pro"
        )
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")
    paths = build_ordered_paths(
        pictures_manifest,
        pictures or [],
        assets_root_for(request),
        user_id=AuthMiddleware.owner_id(request),
    )
    most = settings.pictures.max_pictures
    if not 1 <= len(paths) <= most:
        raise HTTPException(
            status_code=400, detail=f"Add 1 to {most} pictures to recreate"
        )
    if any(classify_kind(p) != "image" for p in paths):
        raise HTTPException(status_code=400, detail="Pictures only")
    if len(chosen.images) + 1 > target.max_images:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{chosen.name} has {len(chosen.images)} pictures; Nano Banana Pro"
                f" on this provider takes {target.max_images - 1} with the new one"
            ),
        )
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
            "resolution": _resolution(spec, target.resolution),
        }
        for path in paths
    ]
    try:
        for params in runs:
            spec.check(_values(target, params))
    except InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    actor = AuthMiddleware.actor(request)
    job_ids = []
    for params in runs:
        job_params = {"provider": provider, "model": target.model, **params}
        job_id = create_job(TASK, job_params, actor=actor)
        enqueue(
            run_job,
            job_id,
            run_picture,
            {
                "job_id": job_id,
                "api_key": api_key.strip(),
                "assets_root": str(assets_root_for(request)),
                **job_params,
            },
        )
        job_ids.append(job_id)
    return JobsQueued(job_ids=job_ids)


def describe(job: Job) -> str:
    """A card's meta line: the preset it ran."""
    name = job.params.get("preset_name")
    return f"{LABEL} · {name}" if name else LABEL


add_job_routes(
    router,
    LABEL,
    lambda _job: "Generating picture…",
    back_url="/pictures",
    back_label="make more",
    show_inputs=True,
)
