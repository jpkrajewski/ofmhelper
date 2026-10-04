"""
/character-sheet: upload several shots of one character (typically 9:16),
get them back as one lossless PNG, side by side in upload order.
"""

import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.utils.character_sheet import combine_side_by_side
from ofmhelpers.web.routers.task_helpers import (
    IMAGE_KINDS,
    UPLOADS_ROOT,
    asset_card,
    convert_heic_upload,
    job_status_payload,
    make_job_dir,
    register_generated_asset,
    require_upload_kind,
    serve_job_file,
)
from ofmhelpers.web.stores.jobs import create_job, get_job, list_jobs_page, run_job
from ofmhelpers.web.templates_config import get_templates

router = APIRouter(prefix="/character-sheet", tags=["character-sheet"])

UPLOAD_ROOT = UPLOADS_ROOT / "character-sheet"

TASK = "character_sheet"

FILES_PREFIX = "/character-sheet/files"

SHEET_NAME = "character_sheet.png"

# One image is not a sheet.
MIN_IMAGES = 2


def _run_combine(job_dir: str, files: list[str]) -> list[dict]:
    directory = Path(job_dir)
    sheet = combine_side_by_side(
        [directory / name for name in files], directory / SHEET_NAME
    )
    # Straight into the reuse picker: a character sheet is a reference image
    # for the generation tools.
    register_generated_asset(sheet)
    return [{"name": sheet.name, "path": str(sheet)}]


def _gallery_card(job: dict) -> dict:
    assets = []
    if job["status"] == "done":
        assets = [
            asset_card(f["name"], idx, f"{FILES_PREFIX}/{job['id']}")
            for idx, f in enumerate(job.get("result") or [])
        ]
    return {
        "job_id": job["id"],
        "status": job["status"],
        "error": job.get("error"),
        "panels": len(job["params"]["files"]),
        "assets": assets,
    }


@router.get("")
def form(request: Request):
    recent, _total = list_jobs_page({TASK}, 0, settings.web.gallery_limit)
    return get_templates().TemplateResponse(
        request,
        "character_sheet.html",
        {
            "gallery": [_gallery_card(j) for j in recent],
            "max_images": settings.web.character_sheet_max_images,
        },
    )


@router.post("/run")
async def run(
    request: Request,
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    uploads = [convert_heic_upload(f) for f in files or [] if f.filename]
    max_images = settings.web.character_sheet_max_images
    if not MIN_IMAGES <= len(uploads) <= max_images:
        raise HTTPException(
            status_code=400,
            detail=f"Upload between {MIN_IMAGES} and {max_images} images",
        )
    names = [require_upload_kind(f.filename, IMAGE_KINDS) for f in uploads]

    # Numbered, not saved under the client's name: iOS names every shared
    # photo "image.jpg", and the number is also what keeps upload order.
    job_dir = make_job_dir(UPLOAD_ROOT)
    saved = []
    for i, (upload, name) in enumerate(zip(uploads, names, strict=True)):
        dest = job_dir / f"{i:02d}_{name}"
        with dest.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
        saved.append(dest.name)

    job_id = create_job(
        TASK, {"dir": str(job_dir), "files": saved}, actor=request.session.get("role")
    )
    enqueue(run_job, job_id, _run_combine, {"job_dir": str(job_dir), "files": saved})
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    card = _gallery_card(job)
    return get_templates().TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": card["assets"],
            "title": "Character sheet",
            "pending_message": f"Combining {card['panels']} image(s)…",
            "back_url": "/character-sheet",
            "back_label": "make another sheet",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    return job_status_payload(get_job(job_id), FILES_PREFIX)


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: int = 0):
    return serve_job_file(get_job(job_id), index, as_attachment=bool(dl))
