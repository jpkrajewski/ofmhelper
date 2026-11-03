"""
/clone: subject images (one person, up to `clone_max_images` photos) + many
videos -> one Wan 3.0 run per video, every run getting all the images; each
run is its own card in the gallery, the same card as /generate's
(`_gallery_card.html`), newest first.

Routes only; what they do is in `features/clone/` (the Wan 3.0 table, pricing,
the submit's checks, cards, Drive picker, export). `_run_clone` stays here: RQ
pickles a job's function by path. `run_ai`, `duration_s` and `_exported` are
looked up here when called and handed on, so this module stays their one seam.
"""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import Field

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import get_job, start_job
from ofmhelpers.features.clone.export import (
    EXPORT_FAILURES,
    ExportFailedError,
    export_choice,
    export_results,
)
from ofmhelpers.features.clone.export import exported_file as _exported
from ofmhelpers.features.clone.pricing import clip_duration, clone_pricing
from ofmhelpers.features.clone.service import (
    FILES_PREFIX,
    PREFIX,
    TASK,
    CloneRun,
    cards,
    check_choices,
    drive_picker,
    plan_runs,
    upload_states,
    video_seconds,
)
from ofmhelpers.features.clone.service import card as _card
from ofmhelpers.features.clone.targets import (
    DEFAULT_RESOLUTION,
    CloneTarget,
    clone_targets,
    offered_resolutions,
)
from ofmhelpers.features.clone.targets import clone_values as _clone_values
from ofmhelpers.features.drive.uploads import queue_uploads, remove_upload
from ofmhelpers.integrations.gdrive import DriveError
from ofmhelpers.media.exports import ExportOptions, exports
from ofmhelpers.media.video_probe import duration_s
from ofmhelpers.schemas import (
    CloneQueued,
    DriveUpload,
    ProviderChoice,
    ResultFile,
    UploadRequest,
    UploadsQueued,
)
from ofmhelpers.web.dependencies import (
    Actor,
    ApiKeys,
    AssetsRoot,
    CurrentUser,
    OwnerId,
)
from ofmhelpers.web.forms import Files, PaidForm
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.routers.generation.ai import run_ai
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    build_ordered_paths,
    keep_videos,
)
from ofmhelpers.web.templates_config import render, render_partial, render_polled

# What other modules and the tests read here (same objects as the package's).
__all__ = [
    "CLONE_TARGETS",
    "DEFAULT_PROMPT",
    "EXPORT_FAILURES",
    "FILES_PREFIX",
    "TASK",
    "CloneTarget",
    "ExportFailedError",
    "_card",
    "_clone_values",
    "_exported",
    "_run_clone",
    "clip_duration",
    "router",
]

router = APIRouter(prefix=PREFIX, tags=["clone"])

DEFAULT_PROMPT = settings.web.clone_default_prompt

CLONE_TARGETS = clone_targets()

gallery = Gallery({TASK})


class CloneForm(PaidForm):
    prompt: str = Field(DEFAULT_PROMPT, min_length=1)
    resolution: str = DEFAULT_RESOLUTION
    audio: bool = False
    export: str = ""
    mirror: bool = False
    image: Files
    image_manifest: str = "[]"
    videos: Files
    videos_manifest: str = "[]"


def _run_clone(
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    prompt: str,
    resolution: str,
    duration: int,
    reference_images: list[str],
    reference_videos: list[str],
    audio: bool = False,
    export: str | None = None,
    mirror: bool = False,
    capcut: bool = False,
    **page_keys: Any,
) -> list[ResultFile]:
    """One clone run. `page_keys` (`model`; older jobs also `clone_batch`,
    `clone_index`) are stored on the job for the page only, but for
    `EXPORT_FAILURES` (earlier runs whose export failed). `capcut` is the
    choice of jobs from before `export` (re-run by recovery)."""
    target = CLONE_TARGETS[provider]
    values = _clone_values(
        target,
        prompt,
        resolution,
        duration,
        audio,
        reference_images,
        reference_videos[0],
    )
    results = run_ai(
        job_id, api_key, assets_root, provider=provider, model=target.model, **values
    )
    return export_results(
        job_id,
        results,
        export_choice(export, capcut),
        ExportOptions(mirror=mirror),
        Path(assets_root),
        failures=int(page_keys.get(EXPORT_FAILURES) or 0),
        exported=_exported,
    )


def _video_seconds(path: str) -> float:
    return video_seconds(path, duration_s)


def _queue(run: CloneRun, api_key: str, assets_root: Path, actor: str) -> str:
    """One run's job. The key goes to the worker only, never into the
    params its page shows."""
    params = run.model_dump()
    return start_job(
        TASK,
        params,
        _run_clone,
        {"api_key": api_key, "assets_root": str(assets_root), **params},
        actor=actor,
    )


def _rendered_cards(job_ids: list[str], actor: str, user_id: int) -> str:
    """These jobs' cards, newest first like the gallery."""
    jobs = [job for j in reversed(job_ids) if (job := get_job(j, actor)) is not None]
    picker = dict(drive_picker(user_id))
    return "".join(
        render_partial("_clone_card.html", job=card, **picker) for card in cards(jobs)
    )


@router.get("")
def form(
    request: Request,
    actor: Actor,
    user: CurrentUser,
    api_keys: ApiKeys,
    assets_root: AssetsRoot,
    video: Annotated[list[str] | None, Query()] = None,
) -> Response:
    first = gallery.page(actor)
    return render(
        request,
        "clone.html",
        gallery=cards(first.jobs),
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(actor),
        providers=ProviderChoice.all(list(CLONE_TARGETS)),
        selected_provider=settings.aigen.default_provider,
        api_keys=api_keys,
        pricing={name: price.model_dump() for name, price in clone_pricing().items()},
        default_prompt=DEFAULT_PROMPT,
        default_resolution=DEFAULT_RESOLUTION,
        resolutions=offered_resolutions(),
        max_videos=settings.web.clone_max_videos,
        max_images=settings.web.clone_max_images,
        live_poll_s=settings.web.gallery_live_poll_s,
        # The `?video=` paths /reels or a download hands over; a stale link
        # just opens with fewer videos picked.
        prefill_videos=keep_videos(video or [], assets_root),
        max_reference_s=min(t.max_reference_s for t in CLONE_TARGETS.values()),
        exports=list(exports().values()),
        default_export=settings.exports.default,
        default_mirror=settings.exports.mirror,
        **dict(drive_picker(user.id)),
    )


@router.get("/gallery")
def gallery_page(
    request: Request,
    user: CurrentUser,
    actor: Actor,
    query: GalleryParams,
) -> Response:
    """The next page of cards, for gallery-scroll.js; `hidden`: only the hidden
    cards (hidden-cards.js's "Show hidden")."""
    found = gallery.page(actor, query)
    return render(
        request,
        "clone_gallery_fragment.html",
        gallery=cards(found.jobs),
        next_offset=found.next_offset,
        **dict(drive_picker(user.id)),
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request, actor: Actor, user: CurrentUser) -> Response:
    """The newest page of cards, no sentinel: what live-gallery.js polls to
    show runs started in other sessions."""
    return render_polled(
        request,
        "clone_gallery_fragment.html",
        gallery=cards(gallery.page(actor).jobs),
        next_offset=None,
        **dict(drive_picker(user.id)),
    )


@router.post("/drive-upload")
def drive_upload(user: CurrentUser, body: UploadRequest) -> UploadsQueued:
    """These runs' videos into each picked account's Drive folder, on the
    worker, as the clicker's Google account."""
    uploads = queue_uploads(user, body.job_ids, body.login_ids)
    return UploadsQueued(
        queued=sum(1 for u in uploads if u.status == "queued"), uploads=uploads
    )


@router.post("/drive-uploads/{upload_id}/remove")
def drive_upload_remove(user: CurrentUser, upload_id: int) -> DriveUpload:
    """Takes an uploaded video back out of its account folder (Drive trash)."""
    try:
        return remove_upload(user, upload_id)
    except DriveError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/drive-uploads")
def drive_upload_states(
    actor: Actor, job: Annotated[list[str], Query(max_length=200)]
) -> list[DriveUpload]:
    """Where these runs' videos are in Drive: what clone.js polls while
    uploads run. Only runs this session may see."""
    return upload_states(job, actor)


@router.post("/run")
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
    choices = check_choices(form.provider, form.api_key, form.export, form.resolution)
    runs = plan_runs(
        choices,
        form.prompt,
        form.audio,
        form.mirror,
        build_ordered_paths(
            form.image_manifest, form.image, assets_root, user_id=owner
        ),
        build_ordered_paths(
            form.videos_manifest, form.videos, assets_root, user_id=owner
        ),
        _video_seconds,
    )
    job_ids = [_queue(r, choices.api_key, assets_root, actor) for r in runs]
    return CloneQueued(job_ids=job_ids, html=_rendered_cards(job_ids, actor, user.id))


add_job_routes(
    router,
    "Clone",
    lambda _job: "Generating video… this can take a few minutes.",
    back_url="/clone",
    back_label="clone more",
    show_inputs=True,
)
