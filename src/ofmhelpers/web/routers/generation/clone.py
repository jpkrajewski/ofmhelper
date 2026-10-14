"""
/clone: one subject image + many videos -> one Wan 3.0 run per video, all
submitted as one batch and shown grouped on the page (`clone_batches.py`).

Each run's duration is its source video's length, clamped to what Wan 3.0
takes. Every video is probed before any job is queued, so a bad file fails the
whole submit up front instead of after some runs were already paid for.
"""

import subprocess
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ofmhelpers.aigenproviders.kaiai.types import ADAPTIVE_ASPECT_RATIO, Wan3Resolution
from ofmhelpers.config import settings
from ofmhelpers.utils.video_probe import duration_s
from ofmhelpers.web.api_keys import get_kie_api_key
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.generation.clone_batches import (
    CloneBatch,
    group_batches,
)
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_job_routes,
    require_api_key,
    start_kie_job,
)
from ofmhelpers.web.routers.generation.wan import _run_wan3
from ofmhelpers.web.routers.task_helpers import (
    assets_root_for,
    build_ordered_paths,
    classify_kind,
)
from ofmhelpers.web.stores.jobs import get_job, list_jobs_page
from ofmhelpers.web.templates_config import get_templates

router = APIRouter(prefix="/clone", tags=["clone"])

TASK = "clone"

DEFAULT_PROMPT = "Replace the subject in @Video1 with subject from @Image1"

DEFAULT_RESOLUTION = Wan3Resolution.P720

# Wan 3.0's accepted duration range, in whole seconds.
MIN_DURATION_S = 2
MAX_DURATION_S = 30


def wan3_duration(seconds: float) -> int:
    """A source video's length as the nearest duration Wan 3.0 accepts."""
    return min(max(round(seconds), MIN_DURATION_S), MAX_DURATION_S)


def _run_clone(clone_batch: str, clone_index: int, **wan3_kwargs: Any) -> list[dict]:
    """The batch keys are stored on the job for grouping only; the generation
    itself is a plain Wan 3.0 run."""
    del clone_batch, clone_index
    return _run_wan3(**wan3_kwargs)


def _video_duration(path: str) -> int:
    try:
        seconds = duration_s(Path(path), settings.web.ffmpeg_timeout_s)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
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
    return group_batches(jobs, page_full=total > limit)


@router.get("")
def form(request: Request):
    return get_templates().TemplateResponse(
        request,
        "clone.html",
        {
            "batches": _recent_batches(request),
            "kie_api_key": get_kie_api_key(request),
            "default_prompt": DEFAULT_PROMPT,
            "default_resolution": DEFAULT_RESOLUTION.value,
            "resolutions": list(Wan3Resolution),
            "max_videos": settings.web.clone_max_videos,
        },
    )


@router.post("/run")
def run(
    request: Request,
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form(min_length=1)] = DEFAULT_PROMPT,
    resolution: Annotated[Wan3Resolution, Form()] = DEFAULT_RESOLUTION,
    audio: Annotated[bool, Form()] = False,
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

    if len(image_paths) != 1:
        raise HTTPException(status_code=400, detail="Pick exactly one subject image")
    max_videos = settings.web.clone_max_videos
    if not 1 <= len(video_paths) <= max_videos:
        raise HTTPException(
            status_code=400, detail=f"Pick between 1 and {max_videos} videos"
        )
    _require_kind(image_paths, "image", "The subject")
    _require_kind(video_paths, "video", "Videos")
    durations = [_video_duration(p) for p in video_paths]

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
                "clone_batch": batch_id,
                "clone_index": index,
            },
            {
                "reference_images": image_paths,
                "reference_videos": [path],
                "reference_audio": [],
            },
        )["job_id"]
        for index, (path, duration) in enumerate(
            zip(video_paths, durations, strict=True)
        )
    ]

    actor = AuthMiddleware.visible_actor(request)
    jobs = [job for j in job_ids if (job := get_job(j, actor)) is not None]
    [batch] = group_batches(jobs, page_full=False)
    html = get_templates().get_template("_clone_batch.html").render(batch=batch)
    return {"job_ids": job_ids, "html": html}


add_job_routes(router, "Clone", "video", back_url="/clone", back_label="clone more")
