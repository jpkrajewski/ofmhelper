import shutil
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Form,
    UploadFile,
    File,
    BackgroundTasks,
    HTTPException,
)
from fastapi.responses import RedirectResponse, FileResponse
import mimetypes
from fastapi import Query

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.aigenproviders.kaiai.client import KieAIClient

router = APIRouter(prefix="/kling3", tags=["kling3"])

UPLOAD_ROOT = Path("uploads") / "kling3-refs"


def _save(job_dir: Path, upload: UploadFile) -> str:
    dest = job_dir / upload.filename
    with dest.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    return str(dest)


def _run_kling3(
    api_key: str,
    prompt: str,
    mode: str,
    aspect_ratio: str,
    duration: str,
    sound: bool,
    image_paths: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)

    image_urls = [client.upload_local_file(p) for p in image_paths]

    out_path = client.generate_video_kling3(
        prompt=prompt,
        image_urls=image_urls or None,
        mode=mode,
        aspect_ratio=aspect_ratio,
        duration=duration,
        sound=sound,
        multi_shots=False,
    )
    return [{"name": out_path.name, "path": str(out_path)}]


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(request, "kling3_form.html", {})


@router.post("/run")
async def run(
    background_tasks: BackgroundTasks,
    api_key: str = Form(...),
    prompt: str = Form(...),
    mode: str = Form("pro"),
    aspect_ratio: str = Form("16:9"),
    duration: str = Form("5"),
    sound: bool = Form(True),
    images: list[UploadFile] = File(default=[]),
):
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")

    job_dir = UPLOAD_ROOT / uuid.uuid4().hex[:8]
    job_dir.mkdir(parents=True, exist_ok=True)

    image_paths = [_save(job_dir, f) for f in images if f.filename]

    params = {
        "prompt": prompt,
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "sound": sound,
    }
    job_id = create_job("kling3", params)
    background_tasks.add_task(
        run_job,
        job_id,
        _run_kling3,
        {
            "api_key": api_key,
            **params,
            "image_paths": image_paths,
        },
    )

    return RedirectResponse(url=f"/kling3/jobs/{job_id}", status_code=303)


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "kling3_form.html", {}, status_code=404
        )

    if job.get("status") == "done":
        for idx, f in enumerate(job["result"]):
            f["index"] = idx

    return templates.TemplateResponse(request, "kling3_status.html", {"job": job})


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: int = Query(0)):
    job = get_job(job_id)
    if job is None or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    files = job["result"]
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(files[index]["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")

    if dl:
        return FileResponse(
            path, filename=path.name, media_type="application/octet-stream"
        )

    media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    return FileResponse(path, media_type=media_type)
