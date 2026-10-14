"""
/clone: one subject image + many videos -> one Wan 3.0 job per video, each
run as if posted from /generate (task "wan3", same params), so the results
also land in the /generate gallery and click-to-reuse works on them. Each
job's duration is its source video's length, clamped to what Wan 3.0 takes.
"""

import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ofmhelpers.aigenproviders.kaiai.types import ADAPTIVE_ASPECT_RATIO, Wan3Resolution
from ofmhelpers.config import settings
from ofmhelpers.utils.video_probe import duration_s
from ofmhelpers.web.api_keys import get_kie_api_key
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.generation.kie_jobs import require_api_key, start_kie_job
from ofmhelpers.web.routers.generation.wan import _run_wan3
from ofmhelpers.web.routers.task_helpers import (
    IMAGE_KINDS,
    VIDEO_KINDS,
    asset_card,
    assets_root_for,
    require_upload_kind,
    save_asset,
)
from ofmhelpers.web.stores.jobs import list_jobs_page
from ofmhelpers.web.templates_config import get_templates

router = APIRouter(prefix="/clone", tags=["clone"])

TASK = "wan3"

FILES_PREFIX = "/wan3/files"

DEFAULT_PROMPT = "Replace the subject in @Video1 with subject from @Image1"

# Wan 3.0's accepted duration range, in whole seconds.
MIN_DURATION_S = 2
MAX_DURATION_S = 30


def wan3_duration(seconds: float) -> int:
    """A source video's length as the nearest duration Wan 3.0 accepts."""
    return min(max(round(seconds), MIN_DURATION_S), MAX_DURATION_S)


def _video_duration(path: str, name: str) -> int:
    try:
        seconds = duration_s(Path(path), settings.web.ffmpeg_timeout_s)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not read the length of {name}"
        ) from exc
    return wan3_duration(seconds)


def _gallery_card(job: dict) -> dict:
    assets = []
    if job["status"] == "done":
        assets = [
            asset_card(
                f["name"],
                idx,
                f"{FILES_PREFIX}/{job['id']}",
                remote_url=f.get("remote_url"),
            )
            for idx, f in enumerate(job.get("result") or [])
        ]
    return {
        "job_id": job["id"],
        "status": job["status"],
        "error": job.get("error"),
        "params": job["params"],
        "assets": assets,
    }


@router.get("")
def form(request: Request):
    recent, _total = list_jobs_page(
        {TASK}, 0, settings.web.gallery_limit, AuthMiddleware.visible_actor(request)
    )
    return get_templates().TemplateResponse(
        request,
        "clone.html",
        {
            "gallery": [_gallery_card(j) for j in recent],
            "kie_api_key": get_kie_api_key(request),
            "default_prompt": DEFAULT_PROMPT,
            "resolutions": list(Wan3Resolution),
        },
    )


@router.post("/run")
def run(
    request: Request,
    api_key: Annotated[str, Form()],
    image: Annotated[UploadFile, File()],
    videos: Annotated[list[UploadFile], File()],
    prompt: Annotated[str, Form()] = DEFAULT_PROMPT,
    resolution: Annotated[Wan3Resolution, Form()] = Wan3Resolution.P1080,
    audio: Annotated[bool, Form()] = True,
):
    require_api_key(api_key)
    require_upload_kind(image.filename, IMAGE_KINDS)
    for video in videos:
        require_upload_kind(video.filename, VIDEO_KINDS)

    assets_root = assets_root_for(request)
    image_path = save_asset(image, assets_root)
    saved = [(save_asset(v, assets_root), v.filename or "video") for v in videos]
    durations = [_video_duration(path, name) for path, name in saved]

    job_ids = [
        start_kie_job(
            request,
            TASK,
            _run_wan3,
            api_key,
            {
                "prompt": prompt,
                "resolution": resolution.value,
                "aspect_ratio": ADAPTIVE_ASPECT_RATIO,
                "duration": duration,
                "audio": audio,
            },
            {
                "reference_images": [image_path],
                "reference_videos": [path],
                "reference_audio": [],
            },
        )["job_id"]
        for (path, _name), duration in zip(saved, durations, strict=True)
    ]
    return {"job_ids": job_ids}
