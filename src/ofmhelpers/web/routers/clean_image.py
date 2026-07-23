from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File, BackgroundTasks, HTTPException

from ofmhelpers.utils.metadata_cleaner import clean_metadata
from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import (
    make_job_dir,
    save_upload,
    asset_card,
    serve_job_file,
    job_status_payload,
)

router = APIRouter(prefix="/clean-images", tags=["clean-images"])

UPLOAD_ROOT = Path("uploads") / "clean-images"


def _run_clean(job_dir: str) -> list[dict]:
    directory = Path(job_dir)
    clean_metadata(directory)
    files = sorted(p for p in directory.iterdir() if p.is_file())
    return [{"name": p.name, "path": str(p)} for p in files]


@router.post("/run")
async def run(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(default=[]),
):
    files = [f for f in files if f.filename]
    if not files:
        raise HTTPException(status_code=400, detail="At least one image is required")

    job_dir = make_job_dir(UPLOAD_ROOT)
    saved_names = [Path(save_upload(job_dir, f)).name for f in files]

    job_id = create_job(
        "clean_images",
        {"dir": str(job_dir), "files": saved_names},
        actor=request.session.get("role"),
    )
    background_tasks.add_task(run_job, job_id, _run_clean, {"job_dir": str(job_dir)})

    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    assets = []
    if job.get("status") == "done":
        assets = [
            asset_card(f["name"], idx, f"/clean-images/files/{job_id}")
            for idx, f in enumerate(job["result"])
        ]

    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "title": "Clean images",
            "pending_message": f"Cleaning {len(job['params']['files'])} image(s)…",
            "back_url": "/download-assets",
            "back_label": "clean more images",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    return job_status_payload(get_job(job_id), "/clean-images/files")


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: int = 0):
    job = get_job(job_id)
    return serve_job_file(job, index, as_attachment=bool(dl))
