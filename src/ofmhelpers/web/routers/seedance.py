import shutil
import uuid
from enum import Enum
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

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.aigenproviders.kaiai.client import KieAIClient

router = APIRouter(prefix="/seedance", tags=["seedance"])

UPLOAD_ROOT = Path("uploads") / "seedance-refs"


class SeedanceModel(str, Enum):
    standard = "bytedance/seedance-2"
    fast = "bytedance/seedance-2-fast"
    mini = "bytedance/seedance-2-mini"


def _save(job_dir: Path, upload: UploadFile) -> str:
    dest = job_dir / upload.filename
    with dest.open("wb") as out:
        shutil.copyfileobj(upload.file, out)
    return str(dest)


def _run_seedance(
    api_key: str,
    prompt: str,
    model: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    generate_audio: bool,
    mode: str,
    first_frame_path: str | None,
    last_frame_path: str | None,
    reference_image_paths: list[str],
    reference_video_paths: list[str],
    reference_audio_paths: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)

    kwargs = dict(
        prompt=prompt,
        model=model,
        resolution=resolution,
        aspect_ratio=aspect_ratio,
        duration=duration,
        generate_audio=generate_audio,
    )

    if mode == "frames":
        kwargs["first_frame_url"] = client.upload_local_file(first_frame_path)
        if last_frame_path:
            kwargs["last_frame_url"] = client.upload_local_file(last_frame_path)
    else:  # mode == "reference"
        if reference_image_paths:
            kwargs["reference_image_urls"] = [
                client.upload_local_file(p) for p in reference_image_paths
            ]
        if reference_video_paths:
            kwargs["reference_video_urls"] = [
                client.upload_local_file(p) for p in reference_video_paths
            ]
        if reference_audio_paths:
            kwargs["reference_audio_urls"] = [
                client.upload_local_file(p) for p in reference_audio_paths
            ]

    out_path = client.generate_video_seedance2(**kwargs)
    return [{"name": out_path.name, "path": str(out_path)}]


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(
        request,
        "seedance_form.html",
        {"models": [m.value for m in SeedanceModel]},
    )


@router.post("/run")
async def run(
    background_tasks: BackgroundTasks,
    api_key: str = Form(...),
    prompt: str = Form(...),
    model: SeedanceModel = Form(SeedanceModel.standard),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9"),
    duration: int = Form(10),
    generate_audio: bool = Form(True),
    mode: str = Form(...),  # "frames" or "reference"
    first_frame: UploadFile | None = File(None),
    last_frame: UploadFile | None = File(None),
    reference_images: list[UploadFile] = File(default=[]),
    reference_videos: list[UploadFile] = File(default=[]),
    reference_audio: list[UploadFile] = File(default=[]),
):
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")

    job_dir = UPLOAD_ROOT / uuid.uuid4().hex[:8]
    job_dir.mkdir(parents=True, exist_ok=True)

    first_frame_path = (
        _save(job_dir, first_frame) if first_frame and first_frame.filename else None
    )
    last_frame_path = (
        _save(job_dir, last_frame) if last_frame and last_frame.filename else None
    )
    ref_image_paths = [_save(job_dir, f) for f in reference_images if f.filename]
    ref_video_paths = [_save(job_dir, f) for f in reference_videos if f.filename]
    ref_audio_paths = [_save(job_dir, f) for f in reference_audio if f.filename]

    # api_key is intentionally excluded from the job's stored params -- it's
    # only ever passed straight through to the background task, never
    # persisted via create_job/get_job (which is what the status page reads).
    params = {
        "prompt": prompt,
        "model": model.value,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "generate_audio": generate_audio,
        "mode": mode,
    }
    job_id = create_job("seedance", params)
    background_tasks.add_task(
        run_job,
        job_id,
        _run_seedance,
        {
            "api_key": api_key,
            **params,
            "first_frame_path": first_frame_path,
            "last_frame_path": last_frame_path,
            "reference_image_paths": ref_image_paths,
            "reference_video_paths": ref_video_paths,
            "reference_audio_paths": ref_audio_paths,
        },
    )

    return RedirectResponse(url=f"/seedance/jobs/{job_id}", status_code=303)


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "seedance_form.html", {}, status_code=404
        )

    if job.get("status") == "done":
        for idx, f in enumerate(job["result"]):
            f["index"] = idx

    return templates.TemplateResponse(request, "seedance_status.html", {"job": job})


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
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
