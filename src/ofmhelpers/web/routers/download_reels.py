from pathlib import Path
from dataclasses import asdict

from fastapi import APIRouter, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import (
    flatten_grouped_results,
    grouped_job_status_payload,
)
from ofmhelpers.downloaders.generic import download_all

router = APIRouter(prefix="/download-videos", tags=["download-videos"])


def _run_downloads(urls: list[str]) -> list[dict]:
    """Runs in the background. Converts DownloadResult dataclasses to plain
    dicts so they're safe to render in Jinja2 / store in the job dict."""
    results = download_all(urls)
    return [asdict(r) for r in results]


def _flatten_paths(job: dict) -> list[Path]:
    """Every output file across every URL in a job, in a stable order.
    The position in this list IS the file's download index -- this is what
    makes the download link safe: the browser never sends a raw filesystem
    path, only a number we look up against data the job already owns."""
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
        "download_videos", {"urls": url_list}, actor=request.session.get("role")
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
        assets, failed_sources = flatten_grouped_results(job, "/download-videos/files")

    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "failed_sources": failed_sources,
            "title": "Download videos",
            "pending_message": f"Downloading {len(job['params']['urls'])} url(s)…",
            "back_url": "/download-assets",
            "back_label": "download more",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    return grouped_job_status_payload(get_job(job_id), "/download-videos/files")


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: int = 0):
    """Streams a completed download to the browser -- inline for previews,
    Content-Disposition: attachment when ?dl=1."""
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
    return FileResponse(path, media_type="video/mp4")
