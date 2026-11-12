"""
/clone: subject images (one person, up to `clone_max_images` photos) + many
videos -> one Wan 3.0 run per video, every run getting all the images; each
run is its own card in the gallery, the same card as /generate's
(`_gallery_card.html`), newest first.

Routes only; what they do is in `features/clone/` (the Wan 3.0 table, pricing,
the submit's checks, cards, Drive picker, export; `run.py`: the worker's
`run_clone`). `_run_clone` is that function under the path jobs queued before
2026-11-11 name. `duration_s` is looked up here when called and handed on.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import Response
from pydantic import Field

from ofmhelpers.config import settings
from ofmhelpers.features.clone.export import (
    EXPORT_FAILURES,
    ExportFailedError,
)
from ofmhelpers.features.clone.page import form_view
from ofmhelpers.features.clone.pricing import clip_duration
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
from ofmhelpers.features.clone.targets import (
    DEFAULT_RESOLUTION,
    CloneTarget,
    clone_targets,
)
from ofmhelpers.features.clone.targets import clone_values as _clone_values
from ofmhelpers.features.drive.uploads import queue_uploads, remove_upload
from ofmhelpers.media.video_probe import duration_s
from ofmhelpers.schemas import (
    CloneQueued,
    DriveUpload,
    UploadRequest,
    UploadsQueued,
)
from ofmhelpers.schemas.access import Action, Area
from ofmhelpers.web.dependencies import (
    Access,
    Actor,
    Actors,
    ApiKeys,
    AssetsRoot,
    Can,
    CurrentUser,
    OwnerId,
    Viewing,
)
from ofmhelpers.web.forms import Files, PaidForm
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render, render_partial, render_polled

# What other modules and the tests read here (same objects as the package's).
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

router = APIRouter(prefix=PREFIX, tags=["clone"], dependencies=[Access(Area.CLONE)])


def default_prompt() -> str:
    """What the prompt box starts with: a setting, read at each use."""
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


def _rendered_cards(job_ids: list[str], actor: str, user_id: int) -> str:
    """These jobs' cards, newest first like the gallery."""
    picker = dict(drive_picker(user_id))
    return "".join(
        render_partial("_clone_card.html", job=card, **picker)
        for card in cards_of(job_ids, actor)
    )


@router.get("")
def form(
    request: Request,
    actor: Actor,
    viewer: Viewing,
    user: CurrentUser,
    api_keys: ApiKeys,
    assets_root: AssetsRoot,
    video: Annotated[list[str] | None, Query()] = None,
) -> Response:
    first = gallery.page(viewer)
    return render(
        request,
        "clone.html",
        gallery=cards(first.jobs),
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(viewer),
        api_keys=api_keys,
        **dict(form_view(actor, assets_root, video or [])),
        **dict(drive_picker(user.id)),
    )


@router.get("/gallery")
def gallery_page(
    request: Request,
    user: CurrentUser,
    viewer: Viewing,
    query: GalleryParams,
) -> Response:
    """The next page of cards, for gallery-scroll.js; `hidden`: only the hidden
    cards (hidden-cards.js's "Show hidden")."""
    found = gallery.page(viewer, query)
    return render(
        request,
        "clone_gallery_fragment.html",
        gallery=cards(found.jobs),
        next_offset=found.next_offset,
        **dict(drive_picker(user.id)),
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request, viewer: Viewing, user: CurrentUser) -> Response:
    """The newest page of cards, no sentinel: what live-gallery.js polls to
    show runs started in other sessions."""
    return render_polled(
        request,
        "clone_gallery_fragment.html",
        gallery=cards(gallery.page(viewer).jobs),
        next_offset=None,
        **dict(drive_picker(user.id)),
    )


@router.post("/drive-upload", dependencies=[Can(Action.DRIVE_UPLOAD)])
def drive_upload(
    user: CurrentUser, actors: Actors, body: UploadRequest
) -> UploadsQueued:
    """These runs' videos (the clicker's own or their VAs') into each picked
    account's Drive folder, on the worker, as the clicker's Google account."""
    uploads = queue_uploads(user, body.job_ids, body.login_ids, actors)
    return UploadsQueued(
        queued=sum(1 for u in uploads if u.status == "queued"), uploads=uploads
    )


@router.post(
    "/drive-uploads/{upload_id}/remove", dependencies=[Can(Action.DRIVE_UPLOAD)]
)
def drive_upload_remove(user: CurrentUser, upload_id: int) -> DriveUpload:
    """Takes an uploaded video back out of its account folder (Drive trash)."""
    return remove_upload(user, upload_id)


@router.get("/drive-uploads")
def drive_upload_states(
    actors: Actors, job: Annotated[list[str], Query(max_length=200)]
) -> list[DriveUpload]:
    """Where these runs' videos are in Drive: what clone.js polls while
    uploads run. Only runs this session may see."""
    return upload_states(job, actors)


@router.post("/run", status_code=202)
def run(
    form: Annotated[CloneForm, Form()],
    actor: Actor,
    owner: OwnerId,
    user: CurrentUser,
    assets_root: AssetsRoot,
) -> CloneQueued:
    """Queues one run per video and returns their job ids plus their cards
    rendered, newest first like the gallery, so the page shows them without
    a reload."""
    submit = Submit(
        actor=actor,
        owner=owner,
        assets_root=assets_root,
        run=_run_clone,
        probe=_video_seconds,
    )
    job_ids = submit.start(form)
    return CloneQueued(job_ids=job_ids, html=_rendered_cards(job_ids, actor, user.id))


add_job_routes(
    router,
    "Clone",
    lambda _job: "Generating video… this can take a few minutes.",
    back_url="/clone",
    back_label="clone more",
    show_inputs=True,
)
