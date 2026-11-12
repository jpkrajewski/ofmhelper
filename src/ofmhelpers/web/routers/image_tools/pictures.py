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

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ofmhelpers.features.pictures import service
from ofmhelpers.features.pictures.service import LABEL, TASK, run_picture
from ofmhelpers.features.pictures.targets import (
    PICTURE_TARGETS,
    PRESET_ASPECTS,
    PictureTarget,
    aspect_of,
    default_provider,
    nearest_aspect,
    run_aspect,
)
from ofmhelpers.features.pictures.targets import resolution as _resolution
from ofmhelpers.schemas import Job, JobsQueued, PicturePreset, TaskCard
from ofmhelpers.schemas.access import Area
from ofmhelpers.web.dependencies import (
    Access,
    Actor,
    ApiKeys,
    AssetsRoot,
    OwnerId,
    Viewing,
)
from ofmhelpers.web.forms import Files, FormModel, PaidForm, Stripped
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render, render_polled

# What other modules and the tests read here (same objects as the feature's).
__all__ = [
    "LABEL",
    "PICTURE_TARGETS",
    "PRESET_ASPECTS",
    "TASK",
    "PictureTarget",
    "_resolution",
    "aspect_of",
    "default_provider",
    "describe",
    "nearest_aspect",
    "router",
    "run_aspect",
    "run_picture",
]

router = APIRouter(
    prefix="/pictures", tags=["pictures"], dependencies=[Access(Area.PICTURES)]
)

gallery = Gallery({TASK})

# Short: an edited preset may put another image under the same index.
_IMAGE_CACHE = {"Cache-Control": "private, max-age=60"}


class PresetForm(FormModel):
    name: Stripped = Field(min_length=1, max_length=80)
    prompt: Stripped = Field(min_length=1, max_length=20000)
    aspect_ratio: str = Field("", max_length=16)
    images: Files


class PresetEditForm(PresetForm):
    keep: list[int] = Field(default_factory=list)


class PresetDeleted(BaseModel):
    deleted: int


# --- the page and its gallery --------------------------------------------------


def _cards(jobs: list[Job]) -> list[TaskCard]:
    from ofmhelpers.web.routers.registry import task_card

    return [task_card(job) for job in jobs]


@router.get("")
def page(
    request: Request,
    owner_id: OwnerId,
    actor: Actor,
    viewer: Viewing,
    api_keys: ApiKeys,
    preset: Annotated[int | None, Query()] = None,
) -> Response:
    first = gallery.page(viewer)
    return render(
        request,
        "pictures.html",
        api_keys=api_keys,
        gallery=_cards(first.jobs),
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(viewer),
        **dict(service.form_view(owner_id, actor, preset)),
    )


@router.get("/gallery")
def gallery_page(request: Request, viewer: Viewing, query: GalleryParams) -> Response:
    found = gallery.page(viewer, query)
    return render(
        request,
        "generate_gallery_fragment.html",
        gallery=_cards(found.jobs),
        next_offset=found.next_offset,
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request, viewer: Viewing) -> Response:
    return render_polled(
        request,
        "generate_gallery_fragment.html",
        gallery=_cards(gallery.page(viewer).jobs),
        next_offset=None,
    )


# --- presets -------------------------------------------------------------------


@router.get("/presets/{preset_id}/images/{index}")
def preset_image(preset_id: int, index: int, owner_id: OwnerId) -> FileResponse:
    """A preset image as a small webp, to whoever may see the preset."""
    return FileResponse(
        service.image_thumb(preset_id, index, owner_id),
        media_type="image/webp",
        headers=_IMAGE_CACHE,
    )


@router.post("/presets", status_code=201)
def create_preset(
    form: Annotated[PresetForm, Form()],
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> PicturePreset:
    return service.create_preset(form, owner_id, assets_root)


@router.post("/presets/{preset_id}")
def update_preset(
    preset_id: int,
    form: Annotated[PresetEditForm, Form()],
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> PicturePreset:
    """`keep`: the indexes of the current images to keep, in order; new
    images go after them."""
    return service.update_preset(preset_id, form, form.keep, owner_id, assets_root)


@router.post("/presets/{preset_id}/delete")
def delete_preset(preset_id: int, owner_id: OwnerId) -> PresetDeleted:
    return PresetDeleted(deleted=service.delete_preset(preset_id, owner_id))


# --- runs ------------------------------------------------------------------------


class PictureRunForm(PaidForm):
    # The app's default provider may have no Nano Banana Pro.
    provider: str = Field(default_factory=default_provider)
    preset: int
    pictures: Files
    pictures_manifest: str = "[]"


@router.post("/run", status_code=202)
def run(
    form: Annotated[PictureRunForm, Form()],
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> JobsQueued:
    """One job per picture to recreate, each checked against the model
    before anything is queued."""
    return service.start(form, run_picture, actor, owner_id, assets_root)


def describe(job: Job) -> str:
    """A card's meta line: the preset it ran."""
    return service.describe_preset(job.params)


add_job_routes(
    router,
    LABEL,
    lambda _job: "Generating picture…",
    back_url="/pictures",
    back_label="make more",
    show_inputs=True,
)
