from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.utils.metadata_cleaner import clean_metadata
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers import (
    IMAGE_KINDS,
    UPLOADS_ROOT,
    add_job_routes,
    assets_root_for,
    convert_heic_upload,
    make_job_dir,
    register_generated_asset,
    require_upload_kind,
    save_upload,
)
from ofmhelpers.web.schemas import JobQueued, ResultFile
from ofmhelpers.web.stores.jobs import create_job, run_job

router = APIRouter(prefix="/clean-images", tags=["clean-images"])

UPLOAD_ROOT = UPLOADS_ROOT / "clean-images"


def _run_clean(job_dir: str, assets_root: str) -> list[ResultFile]:
    directory = Path(job_dir)
    clean_metadata(directory)
    files = sorted(p for p in directory.iterdir() if p.is_file())
    # Adopt the stripped copies into the requester's part of the asset store
    # so they're directly reusable in the generation pickers -- cleaning an
    # image is usually the step right before generating with it.
    for p in files:
        register_generated_asset(p, Path(assets_root))
    return [ResultFile(name=p.name, path=str(p)) for p in files]


@router.post("/run")
def run(
    request: Request,
    files: Annotated[list[UploadFile] | None, File()] = None,
):
    uploads = [convert_heic_upload(f) for f in files or [] if f.filename]
    if not uploads:
        raise HTTPException(status_code=400, detail="At least one image is required")

    # Validate every name before writing any of them: the cleaner skips a
    # file it doesn't recognise rather than failing, which would otherwise
    # leave an unprocessed upload sitting in the job dir and listed in the
    # result (a .html there used to come back as a page on our own origin).
    for f in uploads:
        require_upload_kind(f.filename, IMAGE_KINDS)

    job_dir = make_job_dir(UPLOAD_ROOT)
    saved_names = [Path(save_upload(job_dir, f)).name for f in uploads]

    job_id = create_job(
        "clean_images",
        {"dir": str(job_dir), "files": saved_names},
        actor=AuthMiddleware.actor(request),
    )
    enqueue(
        run_job,
        job_id,
        _run_clean,
        {"job_dir": str(job_dir), "assets_root": str(assets_root_for(request))},
    )

    return JobQueued(job_id=job_id)


add_job_routes(
    router,
    "Clean images",
    lambda job: f"Cleaning {len(job.params['files'])} image(s)…",
    back_url="/download-assets",
    back_label="clean more images",
)
