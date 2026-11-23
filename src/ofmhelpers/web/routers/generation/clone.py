# Copyright (C) 2026 jpkrajewski

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import Field

from ofmhelpers.config import settings
from ofmhelpers.db.clone_examples.schema import ExamplePart
from ofmhelpers.db.drive_folders.schema import DriveUpload, UploadRequest, UploadsQueued
from ofmhelpers.features.assets import send as sending
from ofmhelpers.features.clone import examples
from ofmhelpers.features.clone.export import (
    EXPORT_FAILURES,
    ExportFailedError,
)
from ofmhelpers.features.clone.page import form_view
from ofmhelpers.features.clone.run import run_clone as _run_clone
from ofmhelpers.features.clone.service import (
    FILES_PREFIX,
    PREFIX,
    TASK,
    Submit,
    cards,
    cards_of,
    drive_picker,
    upload_states,
    video_seconds,
)
from ofmhelpers.features.clone.service import card as _card
from ofmhelpers.features.clone_pricing.pricing import clip_duration
from ofmhelpers.features.clone_pricing.targets import (
    DEFAULT_RESOLUTION,
    CloneTarget,
    clone_targets,
)
from ofmhelpers.features.clone_pricing.targets import clone_values as _clone_values
from ofmhelpers.features.jobs.schema import CloneQueued
from ofmhelpers.features.redirect import Redirect
from ofmhelpers.features.storage.service import StorageServiceUploads
from ofmhelpers.media.video_probe import duration_s
from ofmhelpers.web.access import Action, Area
from ofmhelpers.web.dependencies import (
    Access,
    Actor,
    Actors,
    ApiKeys,
    AssetsRoot,
    Can,
    OwnerId,
    Storage,
    Viewing,
)
from ofmhelpers.web.forms import Files, PaidForm
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.idempotency import Idempotent, IdempotentRoute
from ofmhelpers.web.responses import NO_STORE
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render, render_partial, render_polled

__all__ = [
    "CLONE_TARGETS",
    "EXPORT_FAILURES",
    "FILES_PREFIX",
    "TASK",
    "CloneTarget",
    "ExportFailedError",
    "_card",
    "_clone_values",
    "_run_clone",
    "clip_duration",
    "default_prompt",
    "router",
]

router = APIRouter(
    prefix=PREFIX,
    tags=["clone"],
    dependencies=[Access(Area.CLONE)],
    route_class=IdempotentRoute,
)


def default_prompt() -> str:
    return settings.web.clone_default_prompt


CLONE_TARGETS = clone_targets()

gallery = Gallery({TASK})


class CloneForm(PaidForm):
    prompt: str = Field(default_factory=default_prompt, min_length=1)
    resolution: str = DEFAULT_RESOLUTION
    audio: bool = False
    export: str = ""
    mirror: bool = False
    image: Files
    image_manifest: str = "[]"
    videos: Files
    videos_manifest: str = "[]"


def _video_seconds(path: str) -> float:
    return video_seconds(path, duration_s)


def _rendered_cards(
    request: Request,
    job_ids: list[str],
    actor: str,
    uploads: StorageServiceUploads,
) -> str:
    picker = dict(drive_picker(uploads))
    return "".join(
        render_partial("_clone_card.html", {"request": request, "job": card, **picker})
        for card in cards_of(job_ids, actor)
    )


@router.get("")
def form(
    request: Request,
    actor: Actor,
    viewer: Viewing,
    storage: Storage,
    api_keys: ApiKeys,
    assets_root: AssetsRoot,
    query: GalleryParams,
    video: Annotated[list[str] | None, Query()] = None,
) -> Response:
    first = gallery.page(viewer, query)
    context = {
        "gallery": cards(first.jobs),
        "next_offset": first.next_offset,
        "hidden_count": gallery.hidden_count(viewer),
        "dates": query,
        "api_keys": api_keys,
        **dict(form_view(actor, viewer.team, assets_root, video or [])),
        **dict(drive_picker(storage.uploads)),
    }
    response = render(request, "clone.html", context)
    response.headers.update(NO_STORE)
    return response


@router.get("/gallery")
def gallery_page(
    request: Request,
    storage: Storage,
    viewer: Viewing,
    query: GalleryParams,
) -> Response:
    found = gallery.page(viewer, query)
    return render(
        request,
        "clone_gallery_fragment.html",
        {
            "gallery": cards(found.jobs),
            "next_offset": found.next_offset,
            **dict(drive_picker(storage.uploads)),
        },
    )


@router.get("/gallery/latest")
def gallery_latest(
    request: Request,
    viewer: Viewing,
    storage: Storage,
    query: GalleryParams,
) -> Response:
    return render_polled(
        request,
        "clone_gallery_fragment.html",
        {
            "gallery": cards(gallery.page(viewer, query.newest()).jobs),
            "next_offset": None,
            **dict(drive_picker(storage.uploads)),
        },
    )


@router.post("/drive-upload", dependencies=[Can(Action.DRIVE_UPLOAD)])
def drive_upload(
    storage: Storage,
    actors: Actors,
    body: UploadRequest,
) -> UploadsQueued:
    return storage.uploads.queue(body.job_ids, body.login_ids, actors, body.va_ids)


@router.post("/{job_id}/handoffs", summary="Open this run's video in another tool")
def handoff(
    job_id: str,
    target: Annotated[str, Form()],
    actors: Actors,
    assets_root: AssetsRoot,
    index: Annotated[int | None, Form(ge=0)] = None,
) -> Redirect:
    return sending.send(job_id, target, index, actors, assets_root)


@router.post(
    "/drive-uploads/{upload_id}/remove",
    dependencies=[Can(Action.DRIVE_UPLOAD)],
)
def drive_upload_remove(
    storage: Storage,
    actors: Actors,
    upload_id: int,
) -> DriveUpload:
    return storage.uploads.remove(upload_id, actors)


@router.get("/drive-uploads")
def drive_upload_states(
    viewer: Viewing,
    actors: Actors,
    job: Annotated[list[str], Query(max_length=200)],
) -> list[DriveUpload]:
    return upload_states(job, actors, viewer.team_ids)


@router.post("/run", status_code=202, dependencies=[Idempotent])
def run(
    request: Request,
    form: Annotated[CloneForm, Form()],
    actor: Actor,
    owner: OwnerId,
    storage: Storage,
    assets_root: AssetsRoot,
) -> CloneQueued:
    submit = Submit(
        actor=actor,
        owner=owner,
        assets_root=assets_root,
        run=_run_clone,
        probe=_video_seconds,
    )
    job_ids = submit.start(form)
    return CloneQueued(
        job_ids=job_ids,
        html=_rendered_cards(request, job_ids, actor, storage.uploads),
    )


@router.get("/examples", summary="The SOP dialog's examples")
def examples_list(request: Request) -> Response:
    return render(request, "_clone_examples.html", {"examples": examples.shown()})


@router.get(
    "/examples/{example_id}/{part}",
    summary="An example's side-by-side video or its poster",
)
def example_file(example_id: int, part: ExamplePart) -> Response:
    return FileResponse(
        examples.file(example_id, part),
        headers={"Cache-Control": "private, max-age=86400"},
    )


add_job_routes(
    router,
    "Clone",
    lambda _job: "Generating video… this can take a few minutes.",
    back_url="/clone",
    back_label="clone more",
    show_inputs=True,
)
