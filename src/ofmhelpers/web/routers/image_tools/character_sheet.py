"""
/character-sheet: upload several shots of one character (typically 9:16),
get them back as one lossless PNG, side by side in upload order. Admin only;
its sheets land in the admin part of the asset store.
"""

import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.utils.character_sheet import combine_side_by_side
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers import (
    IMAGE_KINDS,
    UPLOADS_ROOT,
    add_job_routes,
    convert_heic_upload,
    job_assets,
    make_job_dir,
    register_generated_asset,
    require_upload_kind,
)
from ofmhelpers.web.schemas import Job, JobCard, JobQueued, ResultFile
from ofmhelpers.web.stores.jobs import create_job, list_jobs_page, run_job
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/character-sheet",
    tags=["character-sheet"],
    dependencies=[Depends(AuthMiddleware.require_admin)],
)

UPLOAD_ROOT = UPLOADS_ROOT / "character-sheet"

TASK = "character_sheet"

FILES_PREFIX = f"{router.prefix}/files"

SHEET_NAME = "character_sheet.png"

# One image is not a sheet.
MIN_IMAGES = 2


def _run_combine(job_dir: str, files: list[str]) -> list[ResultFile]:
    directory = Path(job_dir)
    sheet = combine_side_by_side(
        [directory / name for name in files], directory / SHEET_NAME
    )
    # Straight into the reuse picker: a character sheet is a reference image
    # for the generation tools.
    register_generated_asset(sheet)
    return [ResultFile(name=sheet.name, path=str(sheet))]


def _gallery_card(job: Job) -> JobCard:
    return JobCard.from_job(job, *job_assets(job, FILES_PREFIX))


@router.get("")
def form(request: Request):
    recent, _total = list_jobs_page({TASK}, 0, settings.web.gallery_limit)
    return render(
        request,
        "character_sheet.html",
        gallery=[_gallery_card(j) for j in recent],
        max_images=settings.web.character_sheet_max_images,
    )


@router.post("/run")
def run(
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
    return JobQueued(job_id=job_id)


add_job_routes(
    router,
    "Character sheet",
    lambda job: f"Combining {len(job.params['files'])} image(s)…",
    back_url="/character-sheet",
    back_label="make another sheet",
)
