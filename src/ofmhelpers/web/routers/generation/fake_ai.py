"""
ofmhelpers/web/routers/generation/fake_ai.py

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

import subprocess
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Form,
    Request,
)
from PIL import Image, ImageDraw

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    resolve_reference_uploads,
)
from ofmhelpers.web.schemas import JobQueued, ReferenceUploads, ResultFile
from ofmhelpers.web.stores.jobs import create_job, run_job

router = APIRouter(prefix="/fake-ai", tags=["fake-ai"])

# Same env var + same default KieAIClient.from_env uses -- fake generations
# land right alongside real ones, not in a separate folder.
_kieai_settings = settings.kieai
OUT_DIR = Path(_kieai_settings.out_dir)
VIDEO_DURATION_SECONDS = _kieai_settings.fake_ai_video_duration_seconds


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
) -> list[ResultFile]:
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
            msg = "Fake AI Model: ffmpeg isn't installed/on PATH, can't fake a video"
            raise RuntimeError(msg) from exc
        except subprocess.CalledProcessError as exc:
            msg = (
                f"Fake AI Model: ffmpeg failed building the fake video: "
                f"{exc.stderr.decode(errors='replace')[-500:]}"
            )
            raise RuntimeError(msg) from exc
        finally:
            frame_path.unlink(missing_ok=True)
    else:
        out_path = OUT_DIR / f"{uuid.uuid4().hex[:8]}.png"
        frame.save(out_path)

    return [ResultFile(name=out_path.name, path=str(out_path))]


@router.post("/run")
def run(
    request: Request,
    prompt: Annotated[str, Form()],
    refs: Annotated[ReferenceUploads, Depends(ReferenceUploads.from_form)],
    outcome: Annotated[str, Form()] = "success",  # "success" or "error"
    asset_type: Annotated[str, Form()] = "image",  # "image" or "video"
    delay: Annotated[int, Form()] = 2,
    error_message: Annotated[str, Form()] = "Simulated failure for testing",
):
    # These reference uploads don't feed into the fake generation at all --
    # this only exists so the upload/dedupe/reuse plumbing (the same
    # uploads/assets store seedance/kling3/nanobanana use) has a no-cost tool
    # to exercise.
    reference_paths = resolve_reference_uploads(refs, assets_root_for(request))

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
        {**params, **reference_paths},
        actor=request.session.get("role"),
    )
    enqueue(run_job, job_id, _run_fake_ai, params)

    return JobQueued(job_id=job_id)


add_job_routes(
    router,
    "Fake AI Model",
    lambda _job: "Simulating a generation…",
    back_url="/generate",
    back_label="generate another",
    show_inputs=True,
)
