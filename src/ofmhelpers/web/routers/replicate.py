"""
ofmhelpers/web/routers/replicate.py

The "Replicate" page: paste a reel link (or upload the file), and the
reel_machine module downloads it, extracts frames, transcribes it, and
drafts a Seedance 2.0 prompt package. The draft is shown back in an
editable textarea -- along with the contact sheet and transcript -- so you
can punch up the dialogue before dropping in your character's reference
images and firing the actual generation through the existing KieAIClient
(same as web/routers/seedance.py).

Two job types share this router, dispatched by `job["task"]`:
  - "replicate_intake" -> download/frames/transcribe/draft-script (Stage 1),
    rendered by the dedicated replicate_review.html template.
  - "replicate" -> the final Seedance video generation (Stage 2), rendered
    by the same job_status.html template every other generation tool uses.
"""

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
from ofmhelpers.reel_machine.gender import DEFAULT_GENDER, GENDERS, get_gender
from ofmhelpers.reel_machine.looks import LOOKS
from ofmhelpers.reel_machine.shapes import SHAPES, render_shape
from ofmhelpers.web.auth import get_kie_api_key
from ofmhelpers.web.jobs import create_job, get_job, run_job
from ofmhelpers.web.queue import enqueue
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
from ofmhelpers.web.templates_config import templates

router = APIRouter(prefix="/replicate", tags=["replicate"])

# Where a job's downloaded reel/frames/transcript live -- separate from
# ASSETS_ROOT (uploads/assets), which is only for reusable reference files.
INTAKE_ROOT = Path("uploads") / "replicate_intake"


def _run_replicate_intake(
    source: str,
    shape: str,
    look: str,
    llm_provider: str,
    target: str,
    gender: str,
    work_dir: str,
) -> dict:
    intake = pipeline.intake_reel(source, Path(work_dir))
    duration = pipeline.clamp_duration(intake.duration)
    draft = pipeline.draft_script_full(
        intake,
        shape_key=shape,
        look_key=look,
        duration=duration,
        llm_provider=llm_provider,
        target=target,
        gender=gender,
    )
    return {
        "contact_sheet": str(intake.contact_sheet),
        "transcript": intake.transcript.text,
        "draft_script": draft.script,
        "main_subject": draft.main_subject,
        "shape": shape,
        "look": look,
        "duration": duration,
        "target": target,
        "gender": gender,
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
    # Shape.name is a gender-templated string (e.g. "{noun_cap} x {noun}"),
    # rendered here with the default gender purely for the dropdown label --
    # cosmetic only. The actual generation always renders with whichever
    # gender the user picks on submit (see pipeline.draft_script).
    default_gender = get_gender(DEFAULT_GENDER)
    shape_labels = {
        key: render_shape(shape, default_gender).name for key, shape in SHAPES.items()
    }
    return templates.TemplateResponse(
        request,
        "replicate_form.html",
        {
            "shapes": SHAPES,
            "shape_labels": shape_labels,
            "looks": LOOKS,
            "genders": GENDERS,
        },
    )


@router.post("/intake")
async def intake(
    request: Request,
    source_url: Annotated[str, Form()] = "",
    source_file: Annotated[UploadFile | None, File()] = None,
    shape: Annotated[str, Form()] = pipeline.DEFAULT_SHAPE,
    look: Annotated[str, Form()] = pipeline.DEFAULT_LOOK,
    llm_provider: Annotated[str, Form()] = "template",
    target: Annotated[str, Form()] = "",
    gender: Annotated[str, Form()] = DEFAULT_GENDER,
):
    source = source_url.strip()
    if source_file is not None and source_file.filename:
        upload_dir = make_job_dir(INTAKE_ROOT)
        source = save_upload(upload_dir, source_file)
    if not source:
        raise HTTPException(
            status_code=400, detail="Provide a reel URL or upload a file"
        )

    params = {
        "source": source,
        "shape": shape,
        "look": look,
        "llm_provider": llm_provider,
        "target": target,
        "gender": gender,
    }
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
        return templates.TemplateResponse(
            request,
            "replicate_review.html",
            {
                "job": job,
                "shapes": SHAPES,
                "looks": LOOKS,
                "kie_api_key": get_kie_api_key(request),
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


@router.get("/contact-sheet/{job_id}")
def contact_sheet(job_id: str):
    job = get_job(job_id)
    if (
        job is None
        or job.get("task") != "replicate_intake"
        or job.get("status") != "done"
    ):
        raise HTTPException(status_code=404, detail="Contact sheet not available")

    path = Path(job["result"]["contact_sheet"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Contact sheet no longer exists")
    return FileResponse(path, media_type="image/jpeg")


@router.post("/generate")
async def generate(
    request: Request,
    api_key: Annotated[str, Form()],
    script: Annotated[str, Form()],
    duration: Annotated[int, Form()] = 15,
    resolution: Annotated[str, Form()] = "720p",
    character_images: Annotated[list[UploadFile] | None, File()] = None,
    character_images_manifest: Annotated[str, Form()] = "[]",
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
    job_id = create_job(
        "replicate",
        {**params, "character_images": character_ref_paths},
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
    return serve_job_file(
        job, index, as_attachment=bool(dl), default_media_type="video/mp4"
    )
