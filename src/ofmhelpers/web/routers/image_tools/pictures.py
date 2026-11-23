# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.db.picture_presets.schema import PicturePreset
from ofmhelpers.features.jobs.models import TaskCard
from ofmhelpers.features.jobs.schema import JobsQueued
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
from ofmhelpers.web.access import Area
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
lazy from ofmhelpers.web.routers.registry import task_card

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

router: APIRouter = APIRouter(
    prefix="/pictures",
    tags=["pictures"],
    dependencies=[Access(Area.PICTURES)],
)

gallery = Gallery({TASK})

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


def _cards(jobs: list[Job]) -> list[TaskCard]:
    return [task_card(job) for job in jobs]


@router.get("")
def page(  # noqa: PLR0913, PLR0917
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
        {
            "api_keys": api_keys,
            "gallery": _cards(first.jobs),
            "next_offset": first.next_offset,
            "hidden_count": gallery.hidden_count(viewer),
            **dict(service.form_view(owner_id, actor, preset)),
        },
    )


@router.get("/gallery")
def gallery_page(request: Request, viewer: Viewing, query: GalleryParams) -> Response:
    found = gallery.page(viewer, query)
    return render(
        request,
        "generate_gallery_fragment.html",
        {"gallery": _cards(found.jobs), "next_offset": found.next_offset},
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request, viewer: Viewing) -> Response:
    return render_polled(
        request,
        "generate_gallery_fragment.html",
        {"gallery": _cards(gallery.page(viewer).jobs), "next_offset": None},
    )


@router.get("/presets/{preset_id}/images/{index}")
def preset_image(preset_id: int, index: int, owner_id: OwnerId) -> FileResponse:
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
    return service.update_preset(preset_id, form, form.keep, owner_id, assets_root)


@router.post("/presets/{preset_id}/delete")
def delete_preset(preset_id: int, owner_id: OwnerId) -> PresetDeleted:
    return PresetDeleted(deleted=service.delete_preset(preset_id, owner_id))


class PictureRunForm(PaidForm):
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
    return service.start(form, run_picture, actor, owner_id, assets_root)


def describe(job: Job) -> str:
    return service.describe_preset(job.params)


add_job_routes(
    router,
    LABEL,
    lambda _job: "Generating picture…",
    back_url="/pictures",
    back_label="make more",
    show_inputs=True,
)
