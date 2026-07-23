"""
ofmhelpers/web/routers/fake_ai.py

"Fake AI Model" -- a stand-in for a kie.ai-backed generator (same shape as
nanobanana's /run -> background job -> poll -> inline result flow) that never
calls a real API. You pick the outcome yourself (success / error / a delay
before either) and the asset type (image / video), so you can exercise the
/generate page's pending, success and failure states -- for either media
type -- on demand without burning real credits or waiting on a real
provider. Testing-only -- there's no api_key because there's nothing real
being called.

Output and reference uploads deliberately land in the exact same places the
real models use -- OFM_KIEAI_OUT_DIR for generated output (same env var,
same default, as KieAIClient.from_env) and the shared uploads/assets store
for reference uploads (task_helpers.ASSETS_ROOT) -- so this is a genuine
stand-in for the real upload/output plumbing, not a separate code path.
"""

import os
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Form,
    UploadFile,
    File,
    BackgroundTasks,
    HTTPException,
)
from PIL import Image, ImageDraw

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    build_ordered_paths,
    asset_card,
    job_status_payload,
    serve_job_file,
)

router = APIRouter(prefix="/fake-ai", tags=["fake-ai"])

# Same env var + same default KieAIClient.from_env uses -- fake generations
# land right alongside real ones, not in a separate folder.
OUT_DIR = Path(os.getenv("OFM_KIEAI_OUT_DIR", "/app/kieai_out"))
VIDEO_DURATION_SECONDS = 3


def _build_frame(prompt: str) -> Image.Image:
    img = Image.new("RGB", (768, 768), color=(35, 25, 65))
    draw = ImageDraw.Draw(img)
    draw.text((24, 24), "FAKE AI MODEL", fill=(255, 255, 255))
    draw.text(
        (24, 60), "(this is a placeholder, not a real generation)", fill=(190, 190, 220)
    )
    draw.multiline_text((24, 110), prompt[:500], fill=(255, 255, 255))
    return img


def _run_fake_ai(
    prompt: str, outcome: str, asset_type: str, delay: int, error_message: str
) -> list[dict]:
    if delay > 0:
        time.sleep(delay)

    if outcome == "error":
        raise RuntimeError(error_message or "Fake AI Model: simulated failure")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = _build_frame(prompt)

    if asset_type == "video":
        job_stem = uuid.uuid4().hex[:8]
        frame_path = OUT_DIR / f"{job_stem}.png"
        out_path = OUT_DIR / f"{job_stem}.mp4"
        frame.save(frame_path)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(frame_path),
                    "-t",
                    str(VIDEO_DURATION_SECONDS),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(out_path),
                ],
                capture_output=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "Fake AI Model: ffmpeg isn't installed/on PATH, can't fake a video"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Fake AI Model: ffmpeg failed building the fake video: "
                f"{exc.stderr.decode(errors='replace')[-500:]}"
            ) from exc
        finally:
            frame_path.unlink(missing_ok=True)
    else:
        out_path = OUT_DIR / f"{uuid.uuid4().hex[:8]}.png"
        frame.save(out_path)

    return [{"name": out_path.name, "path": str(out_path)}]


@router.post("/run")
async def run(
    request: Request,
    background_tasks: BackgroundTasks,
    prompt: str = Form(...),
    outcome: str = Form("success"),  # "success" or "error"
    asset_type: str = Form("image"),  # "image" or "video"
    delay: int = Form(2),
    error_message: str = Form("Simulated failure for testing"),
    reference_images: list[UploadFile] = File(default=[]),
    reference_images_manifest: str = Form("[]"),
    reference_videos: list[UploadFile] = File(default=[]),
    reference_videos_manifest: str = Form("[]"),
    reference_audio: list[UploadFile] = File(default=[]),
    reference_audio_manifest: str = Form("[]"),
):
    # These reference uploads don't feed into the fake generation at all --
    # this only exists so the upload/dedupe/reuse plumbing (the same
    # uploads/assets store seedance/kling3/nanobanana use) has a no-cost tool
    # to exercise.
    reference_image_paths = build_ordered_paths(
        reference_images_manifest, reference_images, ASSETS_ROOT
    )
    reference_video_paths = build_ordered_paths(
        reference_videos_manifest, reference_videos, ASSETS_ROOT
    )
    reference_audio_paths = build_ordered_paths(
        reference_audio_manifest, reference_audio, ASSETS_ROOT
    )

    params = {
        "prompt": prompt,
        "outcome": outcome,
        "asset_type": asset_type,
        "delay": delay,
        "error_message": error_message,
    }
    # Stored params carry the reference paths (keyed by picker field name)
    # like every real tool, so /generate's click-to-reuse restore path can be
    # tested end to end without spending credits. The background task only
    # gets the scalar params -- the refs never feed the fake generation.
    job_id = create_job(
        "fake_ai",
        {
            **params,
            "reference_images": reference_image_paths,
            "reference_videos": reference_video_paths,
            "reference_audio": reference_audio_paths,
        },
        actor=request.session.get("role"),
    )
    background_tasks.add_task(run_job, job_id, _run_fake_ai, params)

    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    assets = []
    if job.get("status") == "done":
        assets = [
            asset_card(f["name"], idx, f"/fake-ai/files/{job_id}")
            for idx, f in enumerate(job["result"])
        ]

    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "title": "Fake AI Model",
            "pending_message": "Simulating a generation…",
            "back_url": "/generate",
            "back_label": "generate another",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    return job_status_payload(get_job(job_id), "/fake-ai/files")


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
    job = get_job(job_id)
    return serve_job_file(job, index)
