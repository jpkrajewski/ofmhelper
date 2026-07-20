from pathlib import Path
from dataclasses import asdict

from fastapi import APIRouter, Request, Form, BackgroundTasks, HTTPException
from fastapi.responses import RedirectResponse, FileResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
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


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(request, "download_form.html", {})


@router.post("/run")
def run(background_tasks: BackgroundTasks, urls: str = Form(...)):
    url_list = [u.strip() for u in urls.splitlines() if u.strip()]

    job_id = create_job("download_videos", {"urls": url_list})
    background_tasks.add_task(run_job, job_id, _run_downloads, {"urls": url_list})

    return RedirectResponse(url=f"/download-videos/jobs/{job_id}", status_code=303)


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "download_form.html", {}, status_code=404
        )

    # Attach a download index to each output path so the template can
    # link straight to /files/<job_id>/<index> without exposing real paths.
    if job.get("status") == "done":
        idx = 0
        for r in job["result"]:
            r["downloads"] = []
            for p in r["output_paths"]:
                r["downloads"].append({"name": Path(p).name, "index": idx})
                idx += 1

    return templates.TemplateResponse(request, "job_status.html", {"job": job})


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
    """Streams a completed download to the browser as a real file download
    (Content-Disposition: attachment), not just a server-side path."""
    job = get_job(job_id)
    if job is None or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    paths = _flatten_paths(job)
    if index < 0 or index >= len(paths):
        raise HTTPException(status_code=404, detail="File not found")

    path = paths[index]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")

    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
