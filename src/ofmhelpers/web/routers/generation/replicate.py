"""
ofmhelpers/web/routers/generation/replicate.py

The "Replicate" page: paste a reel link (or upload the file) and nothing
else. The reel_machine module downloads it, hands the video plus one fixed
analysis prompt to Gemini, and validates the Seedance 2.0 prompt JSON that
comes back. The review page plays the source video next to that JSON in an
editable textarea, so you can tweak it before dropping in your character's
reference images and firing the real generation through KieAIClient (same as
web/routers/generation/seedance.py).

There are no shape/look/gender/persona/provider inputs: everything that used
to be assembled locally now comes out of the model's own reading of the
video (see reel_machine/CLAUDE.md). The provider is an env-var deployment
choice (`REEL_MACHINE_LLM_PROVIDER`), not a per-job form field.

Two job types share this router, dispatched by `job["task"]`:
  - "replicate_intake" -> download + analyze (Stage 1), rendered by the
    dedicated replicate_review.html template.
  - "replicate" -> the final Seedance video generation (Stage 2), rendered
    by the same job_status.html template every other generation tool uses.
"""

import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse

from ofmhelpers.reel_machine import generation, pipeline
from ofmhelpers.web.auth import get_elevenlabs_api_key, get_kie_api_key
from ofmhelpers.web.queue import enqueue
from ofmhelpers.web.routers.helpers.elevenlabs import VOICES as ELEVENLABS_VOICES
from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    asset_card,
    build_ordered_paths,
    job_inputs,
    job_status_payload,
    make_job_dir,
    save_upload,
    serve_job_file,
)
from ofmhelpers.web.stores.jobs import create_job, get_job, list_jobs, run_job
from ofmhelpers.web.templates_config import templates

router = APIRouter(prefix="/replicate", tags=["replicate"])

# Where a job's downloaded reel lives -- separate from ASSETS_ROOT
# (uploads/assets), which is only for reusable reference files.
INTAKE_ROOT = Path("uploads") / "replicate_intake"


def _latest_child_job(source_job_id: str, task: str) -> dict | None:
    """Most recent job of `task` (e.g. "replicate" or "elevenlabs") that was
    generated *from* this intake job -- so the review page can re-render its
    own last result on reload instead of a page refresh losing it (results
    otherwise only ever existed in the client-side generation.js card).
    list_jobs() is newest-first, so the first match is the latest run."""
    for job in list_jobs():
        if (
            job["task"] == task
            and (job.get("params") or {}).get("source_job_id") == source_job_id
        ):
            return job
    return None


def _run_replicate_intake(source: str, work_dir: str) -> dict:
    analysis = pipeline.analyze(source, Path(work_dir))
    # Both shapes of the prompt are stored on purpose: `prompt` is the
    # validated object (what the Action log records, and proof the response
    # passed schema.parse_analysis), `prompt_text` is the exact string the
    # review textarea shows and Seedance is given. A response that failed
    # validation still lands here -- `prompt` is null, `analysis_error` says
    # why, and `prompt_text` is the provider's raw answer for the VA to fix.
    return {
        "video_path": str(analysis.video_path),
        "duration": analysis.duration,
        "provider": analysis.provider,
        "prompt": analysis.prompt.model_dump() if analysis.prompt else None,
        "prompt_text": analysis.prompt_text,
        "speech": analysis.speech,
        "analysis_error": analysis.error,
    }


def _run_replicate_generate(
    api_key: str,
    prompt: str,
    duration: int,
    resolution: str,
    character_ref_paths: list[str],
) -> list[dict]:
    out_path = generation.generate_reel_clone(
        api_key=api_key,
        prompt=prompt,
        character_ref_paths=character_ref_paths,
        duration=duration,
        resolution=resolution,
    )
    return [{"name": out_path.name, "path": str(out_path)}]


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(request, "replicate_form.html", {})


@router.post("/intake")
async def intake(
    request: Request,
    source_url: Annotated[str, Form()] = "",
    source_file: Annotated[UploadFile | None, File()] = None,
):
    source = source_url.strip()
    if source_file is not None and source_file.filename:
        upload_dir = make_job_dir(INTAKE_ROOT)
        source = save_upload(upload_dir, source_file)
    if not source:
        raise HTTPException(
            status_code=400, detail="Provide a reel URL or upload a file"
        )

    params = {"source": source}
    job_id = create_job("replicate_intake", params, actor=request.session.get("role"))
    enqueue(
        run_job,
        job_id,
        _run_replicate_intake,
        {**params, "work_dir": str(INTAKE_ROOT / job_id)},
    )
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["task"] == "replicate_intake":
        video_job = _latest_child_job(job_id, "replicate")
        voice_job = _latest_child_job(job_id, "elevenlabs")
        video_assets = (
            [
                asset_card(
                    f["name"],
                    idx,
                    f"/replicate/files/{video_job['id']}",
                    source="Seedance video",
                )
                for idx, f in enumerate(video_job["result"])
            ]
            if video_job and video_job.get("status") == "done"
            else []
        )
        voice_assets = (
            [
                asset_card(
                    f["name"],
                    idx,
                    f"/helpers/elevenlabs/files/{voice_job['id']}",
                    source="ElevenLabs voice",
                )
                for idx, f in enumerate(voice_job["result"])
            ]
            if voice_job and voice_job.get("status") == "done"
            else []
        )
        return templates.TemplateResponse(
            request,
            "replicate_review.html",
            {
                "job": job,
                "kie_api_key": get_kie_api_key(request),
                # The review page can send the subject's speech straight to
                # /helpers/elevenlabs/run, so it needs that tool's voices.
                "voices": list(ELEVENLABS_VOICES),
                "elevenlabs_api_key": get_elevenlabs_api_key(),
                # Re-renders the last video/voice generation made from this
                # intake job, so a page reload doesn't lose a result that
                # only ever existed in a client-side generation.js card.
                "video_job": video_job,
                "voice_job": voice_job,
                "video_assets": video_assets,
                "voice_assets": voice_assets,
            },
        )

    assets = []
    if job.get("status") == "done":
        assets = [
            asset_card(f["name"], idx, f"/replicate/files/{job_id}")
            for idx, f in enumerate(job["result"])
        ]
    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "job_inputs": job_inputs(job),
            "title": "Replicate",
            "pending_message": "Generating video… this can take a few minutes.",
            "back_url": "/replicate",
            "back_label": "clone another reel",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["task"] == "replicate_intake":
        return {
            "job_id": job["id"],
            "task": job["task"],
            "status": job["status"],
            "error": job.get("error"),
        }
    return job_status_payload(job, "/replicate/files")


@router.get("/video/{job_id}")
def source_video(job_id: str):
    """Streams the downloaded source reel back for the review page's <video>
    player -- the analysis is all about motion and timing, so reviewing it
    against a grid of stills was never good enough."""
    job = get_job(job_id)
    if (
        job is None
        or job.get("task") != "replicate_intake"
        or job.get("status") != "done"
    ):
        raise HTTPException(status_code=404, detail="Source video not available")

    path = Path(job["result"]["video_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Source video no longer exists")
    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    return FileResponse(path, media_type=media_type)


@router.post("/generate")
async def generate(
    request: Request,
    api_key: Annotated[str, Form()],
    script: Annotated[str, Form()],
    duration: Annotated[int, Form()] = 15,
    resolution: Annotated[str, Form()] = "720p",
    character_images: Annotated[list[UploadFile] | None, File()] = None,
    character_images_manifest: Annotated[str, Form()] = "[]",
    source_job_id: Annotated[str, Form()] = "",
):
    if character_images is None:
        character_images = []
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")
    if not script.strip():
        raise HTTPException(status_code=400, detail="Script is required")

    character_ref_paths = build_ordered_paths(
        character_images_manifest, character_images, ASSETS_ROOT
    )

    params = {"prompt": script, "duration": duration, "resolution": resolution}
    stored_params = {**params, "character_images": character_ref_paths}
    if source_job_id:
        # Lets the review page find and re-render its own last generation on
        # reload (see _latest_child_job) -- not used by the generation call.
        stored_params["source_job_id"] = source_job_id
    job_id = create_job(
        "replicate",
        stored_params,
        actor=request.session.get("role"),
    )
    enqueue(
        run_job,
        job_id,
        _run_replicate_generate,
        {"api_key": api_key, **params, "character_ref_paths": character_ref_paths},
    )
    return {"job_id": job_id}


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: Annotated[int, Query()] = 0):
    job = get_job(job_id)
    return serve_job_file(job, index, as_attachment=bool(dl))
