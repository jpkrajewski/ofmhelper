from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import RedirectResponse

from ofmhelpers.utils.metadata_cleaner import clean_metadata
from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import (
    make_job_dir,
    save_upload,
    attach_download_indexes,
    serve_job_file,
)

router = APIRouter(prefix="/clean-images", tags=["clean-images"])

UPLOAD_ROOT = Path("uploads") / "clean-images"


def _run_clean(job_dir: str) -> list[dict]:
    directory = Path(job_dir)
    clean_metadata(directory)
    files = sorted(p for p in directory.iterdir() if p.is_file())
    return [{"name": p.name, "path": str(p)} for p in files]


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(request, "clean_images_form.html", {})


@router.post("/run")
async def run(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    job_dir = make_job_dir(UPLOAD_ROOT)
    saved_names = [save_upload(job_dir, f).split("/")[-1] for f in files]

    job_id = create_job("clean_images", {"dir": str(job_dir), "files": saved_names})
    background_tasks.add_task(run_job, job_id, _run_clean, {"job_dir": str(job_dir)})

    return RedirectResponse(url=f"/clean-images/jobs/{job_id}", status_code=303)


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "clean_images_form.html", {}, status_code=404
        )
    attach_download_indexes(job)
    return templates.TemplateResponse(request, "clean_images_status.html", {"job": job})


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
    job = get_job(job_id)
    return serve_job_file(job, index)
