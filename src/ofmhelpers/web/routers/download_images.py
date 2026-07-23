import mimetypes
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import (
    flatten_grouped_results,
    grouped_job_status_payload,
)
from ofmhelpers.downloaders.images import download_all

router = APIRouter(prefix="/download-images", tags=["download-images"])


def _run_downloads(urls: list[str]) -> list[dict]:
    results = download_all(urls)
    return [asdict(r) for r in results]


def _flatten_paths(job: dict) -> list[Path]:
    paths: list[Path] = []
    for r in job.get("result") or []:
        for p in r.get("output_paths", []):
            paths.append(Path(p))
    return paths


@router.post("/run")
def run(request: Request, background_tasks: BackgroundTasks, urls: str = Form(...)):
    url_list = [u.strip() for u in urls.splitlines() if u.strip()]
    if not url_list:
        raise HTTPException(status_code=400, detail="At least one URL is required")

    job_id = create_job(
        "download_images", {"urls": url_list}, actor=request.session.get("role")
    )
    background_tasks.add_task(run_job, job_id, _run_downloads, {"urls": url_list})

    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    assets, failed_sources = [], []
    if job.get("status") == "done":
        assets, failed_sources = flatten_grouped_results(job, "/download-images/files")

    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "failed_sources": failed_sources,
            "title": "Download images",
            "pending_message": f"Downloading {len(job['params']['urls'])} url(s)…",
            "back_url": "/download-assets",
            "back_label": "download more",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    return grouped_job_status_payload(get_job(job_id), "/download-images/files")


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: int = 0):
    job = get_job(job_id)
    if job is None or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    paths = _flatten_paths(job)
    if index < 0 or index >= len(paths):
        raise HTTPException(status_code=404, detail="File not found")

    path = paths[index]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")

    if dl:
        return FileResponse(
            path, filename=path.name, media_type="application/octet-stream"
        )

    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return FileResponse(path, media_type=media_type)
