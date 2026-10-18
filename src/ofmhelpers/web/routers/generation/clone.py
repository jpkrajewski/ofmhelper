"""
/clone: subject images (one person, up to `clone_max_images` photos) + many
videos -> one Wan 3.0 run per video, every run getting all the images, all
submitted as one batch and shown grouped on the page (`schemas.CloneBatch`).

Each run's duration is its source video's length, clamped to what Wan 3.0
takes. Every video is probed before any job is queued, so a bad file fails the
whole submit up front instead of after some runs were already paid for.

With `capcut` on (the default), each generated video is then re-edited and
re-encoded as a CapCut export (`utils.capcut`) and that copy is the job's
result; kie.ai's hosted original is dropped from it so view/download serve
the CapCut copy.

With a Google Drive connected (routers/drive.py), each finished video is
also uploaded into it and the card links there.
"""

import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError

from ofmhelpers.aigenproviders.kaiai.types import (
    ADAPTIVE_ASPECT_RATIO,
    WAN3_MAX_DURATION_S,
    WAN3_MAX_REFERENCE_S,
    WAN3_MIN_DURATION_S,
    Wan3Resolution,
)
from ofmhelpers.config import settings
from ofmhelpers.gdrive import upload_file
from ofmhelpers.log import get_logger
from ofmhelpers.utils.capcut import DEFAULT_PRESET, apply_preset
from ofmhelpers.utils.video_probe import duration_s
from ofmhelpers.web.api_keys import get_kie_api_key
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.drive import session_drive
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_kie_job_routes,
    require_api_key,
    start_kie_job,
)
from ofmhelpers.web.routers.generation.wan import _run_wan3
from ofmhelpers.web.routers.task_helpers import (
    assets_root_for,
    build_ordered_paths,
    classify_kind,
    job_assets,
    register_generated_asset,
    resolve_existing_ref,
    strip_asset_hash_prefix,
)
from ofmhelpers.web.schemas import CloneBatch, CloneCard, CloneQueued, Job, ResultFile
from ofmhelpers.web.stores.drive_connections import get_connection
from ofmhelpers.web.stores.jobs import get_job, list_jobs_page
from ofmhelpers.web.templates_config import render, render_partial

logger = get_logger(__name__)

router = APIRouter(prefix="/clone", tags=["clone"])

FILES_PREFIX = f"{router.prefix}/files"

TASK = "clone"

DEFAULT_PROMPT = settings.web.clone_default_prompt

DEFAULT_RESOLUTION = Wan3Resolution.P720


def wan3_duration(seconds: float) -> int:
    """A source video's length as the nearest duration Wan 3.0 accepts."""
    return min(max(round(seconds), WAN3_MIN_DURATION_S), WAN3_MAX_DURATION_S)


def _run_clone(
    clone_batch: str,
    clone_index: int,
    capcut: bool = False,
    drive_connection: str | None = None,
    **wan3_kwargs: Any,
) -> list[ResultFile]:
    """The batch keys are stored on the job for grouping only; the generation
    itself is a plain Wan 3.0 run. `capcut` defaults off so jobs queued before
    the option existed run as they always did."""
    del clone_batch, clone_index
    results = _run_wan3(**wan3_kwargs)
    if capcut:
        assets_root = Path(wan3_kwargs["assets_root"])
        results = [_capcut_result(r, assets_root) for r in results]
    if drive_connection:
        results = [_drive_result(r, drive_connection) for r in results]
    return results


def _drive_result(result: ResultFile, connection_id: str) -> ResultFile:
    """`result` with its Google Drive link once uploaded. Like CapCut, a
    failed upload (or a Drive disconnected meanwhile) keeps the result as
    it is: the video is paid for and on disk either way."""
    connection = get_connection(connection_id)
    if result.path is None or connection is None:
        return result
    try:
        link = upload_file(connection.refresh_token, Path(result.path))
    except (GoogleAuthError, HttpError, OSError):
        logger.warning("drive upload failed for %s", result.path, exc_info=True)
        return result
    return result.model_copy(update={"drive_url": link})


def _capcut_result(result: ResultFile, assets_root: Path) -> ResultFile:
    """The CapCut copy of one generated video, next to the original and
    registered as a reusable asset like it. A remote-only result (the local
    download failed) has nothing to re-encode and is kept as it is, and so
    is the original when the encode fails: the generation was paid for and
    is already on disk, so a post-processing error must not lose it."""
    if result.path is None:
        return result
    src = Path(result.path)
    dst = src.with_name(f"{src.stem}_capcut.mp4")
    try:
        apply_preset(src, dst, DEFAULT_PRESET, settings.web.clone_capcut_timeout_s)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        logger.warning(
            "capcut encode failed, keeping the original %s", src, exc_info=True
        )
        return result
    register_generated_asset(dst, assets_root)
    return ResultFile(name=dst.name, path=str(dst), remote_url=None)


def _video_duration(path: str) -> int:
    try:
        seconds = duration_s(Path(path), settings.web.ffmpeg_timeout_s)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        name = Path(path).name.split("__", 1)[-1]
        raise HTTPException(
            status_code=400, detail=f"Could not read the length of {name}"
        ) from exc
    return wan3_duration(seconds)


def _require_kind(paths: list[str], kind: str, label: str) -> None:
    wrong = [p for p in paths if classify_kind(p) != kind]
    if wrong:
        raise HTTPException(status_code=400, detail=f"{label} must be {kind} files")


def _recent_batches(request: Request) -> list[CloneBatch]:
    limit = settings.web.clone_gallery_limit
    jobs, total = list_jobs_page(
        {TASK}, 0, limit, AuthMiddleware.visible_actor(request)
    )
    return CloneBatch.group([_card(j) for j in jobs], page_full=total > limit)


def _card(job: Job) -> CloneCard:
    video = job.params["reference_videos"][0]
    files = job.result_files()
    return CloneCard.from_job(
        job,
        *job_assets(job, FILES_PREFIX),
        video_name=strip_asset_hash_prefix(Path(video).name),
        drive_url=files[0].drive_url if files else None,
    )


def _prefill_videos(request: Request, paths: list[str]) -> list[str]:
    """The `?video=` paths /reels hands over, kept only where they are a
    video in this session's part of the store; a stale link just opens
    with fewer videos picked."""
    assets_root = assets_root_for(request)
    kept: list[str] = []
    for path in paths:
        try:
            resolved = resolve_existing_ref(path, assets_root)
        except HTTPException:
            continue
        if classify_kind(resolved.name) == "video":
            kept.append(str(resolved))
    return kept


@router.get("")
def form(request: Request, video: Annotated[list[str] | None, Query()] = None):
    return render(
        request,
        "clone.html",
        batches=_recent_batches(request),
        kie_api_key=get_kie_api_key(request),
        default_prompt=DEFAULT_PROMPT,
        default_resolution=DEFAULT_RESOLUTION.value,
        resolutions=list(Wan3Resolution),
        max_videos=settings.web.clone_max_videos,
        max_images=settings.web.clone_max_images,
        live_poll_s=settings.web.gallery_live_poll_s,
        prefill_videos=_prefill_videos(request, video or []),
        credits_per_video_s=settings.web.clone_credits_per_video_s,
        usd_per_credit=settings.web.kie_usd_per_credit,
        max_reference_s=WAN3_MAX_REFERENCE_S,
        drive=session_drive(request),
        drive_configured=settings.gdrive.configured,
        drive_folder=settings.gdrive.folder_name,
    )


@router.get("/batches")
def batches(request: Request):
    """The recent batches as a fragment: what live-gallery.js polls to show
    batches started in other sessions."""
    return render(
        request, "clone_batches_fragment.html", batches=_recent_batches(request)
    )


@router.post("/run")
def run(
    request: Request,
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form(min_length=1)] = DEFAULT_PROMPT,
    resolution: Annotated[Wan3Resolution, Form()] = DEFAULT_RESOLUTION,
    audio: Annotated[bool, Form()] = False,
    capcut: Annotated[bool, Form()] = False,
    image: Annotated[list[UploadFile] | None, File()] = None,
    image_manifest: Annotated[str, Form()] = "[]",
    videos: Annotated[list[UploadFile] | None, File()] = None,
    videos_manifest: Annotated[str, Form()] = "[]",
):
    """Queues the batch and returns its job ids plus the batch rendered as
    the page's group partial, so the page shows it without a reload."""
    require_api_key(api_key)
    assets_root = assets_root_for(request)
    image_paths = build_ordered_paths(image_manifest, image or [], assets_root)
    video_paths = build_ordered_paths(videos_manifest, videos or [], assets_root)

    max_images = settings.web.clone_max_images
    if not 1 <= len(image_paths) <= max_images:
        raise HTTPException(
            status_code=400, detail=f"Pick between 1 and {max_images} subject images"
        )
    max_videos = settings.web.clone_max_videos
    if not 1 <= len(video_paths) <= max_videos:
        raise HTTPException(
            status_code=400, detail=f"Pick between 1 and {max_videos} videos"
        )
    _require_kind(image_paths, "image", "The subject")
    _require_kind(video_paths, "video", "Videos")
    # Probed side by side (each is its own ffprobe process), in picked order;
    # the first unreadable video still fails the whole submit.
    with ThreadPoolExecutor(settings.web.clone_probe_workers) as pool:
        durations = list(pool.map(_video_duration, video_paths))

    drive = session_drive(request)
    batch_id = uuid.uuid4().hex[:12]
    job_ids = [
        start_kie_job(
            request,
            TASK,
            _run_clone,
            api_key,
            {
                "prompt": prompt,
                "resolution": resolution.value,
                "aspect_ratio": ADAPTIVE_ASPECT_RATIO,
                "duration": duration,
                "audio": audio,
                "capcut": capcut,
                "drive_connection": drive.id if drive else None,
                "clone_batch": batch_id,
                "clone_index": index,
            },
            {
                "reference_images": image_paths,
                "reference_videos": [path],
                "reference_audio": [],
            },
        ).job_id
        for index, (path, duration) in enumerate(
            zip(video_paths, durations, strict=True)
        )
    ]

    actor = AuthMiddleware.visible_actor(request)
    jobs = [job for j in job_ids if (job := get_job(j, actor)) is not None]
    [batch] = CloneBatch.group([_card(j) for j in jobs], page_full=False)
    html = render_partial("_clone_batch.html", batch=batch)
    return CloneQueued(job_ids=job_ids, html=html)


add_kie_job_routes(router, "Clone", "video", back_url="/clone", back_label="clone more")
