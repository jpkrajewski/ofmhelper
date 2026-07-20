import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, FileResponse

from ofmhelpers.utils.metadata_cleaner import clean_metadata
from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job

router = APIRouter(prefix="/clean-images", tags=["clean-images"])

UPLOAD_ROOT = Path("uploads") / "clean-images"


def _run_clean(job_dir: str) -> list[dict]:
    """Runs in the background. Cleans the uploaded folder in place, then
    reports back whatever files are left in it (the cleaned images)."""
    directory = Path(job_dir)
    clean_metadata(directory)
    files = sorted(p for p in directory.iterdir() if p.is_file())
    return [{"name": p.name, "path": str(p)} for p in files]


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(request, "clean_images_form.html", {})


@router.post("/run")
async def run(background_tasks: BackgroundTasks, files: list[UploadFile] = File(...)):
    job_dir = UPLOAD_ROOT / uuid.uuid4().hex[:8]
    job_dir.mkdir(parents=True, exist_ok=True)

    saved_names = []
    for f in files:
        dest = job_dir / f.filename
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        saved_names.append(f.filename)

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

    if job.get("status") == "done":
        for idx, f in enumerate(job["result"]):
            f["index"] = idx

    return templates.TemplateResponse(request, "clean_images_status.html", {"job": job})


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
    """Same safe pattern as the video downloader: the link only ever
    carries a job id + integer index, never a raw filesystem path."""
    job = get_job(job_id)
    if job is None or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    files = job["result"]
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(files[index]["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")

    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
