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
    Query,
)
from fastapi.responses import RedirectResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import (
    make_job_dir,
    save_upload,
    build_ordered_paths,
    attach_download_indexes,
    serve_job_file,
)
from ofmhelpers.aigenproviders.kaiai.client import KieAIClient

router = APIRouter(prefix="/seedance", tags=["seedance"])

UPLOAD_ROOT = Path("uploads") / "seedance-refs"
ALLOWED_REF_ROOT = Path("uploads")


class SeedanceModel(str, Enum):
    standard = "bytedance/seedance-2"
    fast = "bytedance/seedance-2-fast"
    mini = "bytedance/seedance-2-mini"


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
    reference_images_manifest: str = Form("[]"),
    reference_videos: list[UploadFile] = File(default=[]),
    reference_videos_manifest: str = Form("[]"),
    reference_audio: list[UploadFile] = File(default=[]),
    reference_audio_manifest: str = Form("[]"),
):
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")

    job_dir = make_job_dir(UPLOAD_ROOT)

    # first/last frame are single, non-reorderable slots -- no manifest needed,
    # just save whatever was actually uploaded.
    first_frame_path = (
        save_upload(job_dir, first_frame)
        if first_frame and first_frame.filename
        else None
    )
    last_frame_path = (
        save_upload(job_dir, last_frame) if last_frame and last_frame.filename else None
    )

    # reference_* are ordered, reusable multi-file lists -- these go through
    # the manifest so previously-uploaded refs get reused by path instead of
    # re-uploaded.
    reference_image_paths = build_ordered_paths(
        job_dir, reference_images_manifest, reference_images, ALLOWED_REF_ROOT
    )
    reference_video_paths = build_ordered_paths(
        job_dir, reference_videos_manifest, reference_videos, ALLOWED_REF_ROOT
    )
    reference_audio_paths = build_ordered_paths(
        job_dir, reference_audio_manifest, reference_audio, ALLOWED_REF_ROOT
    )

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
            "reference_image_paths": reference_image_paths,
            "reference_video_paths": reference_video_paths,
            "reference_audio_paths": reference_audio_paths,
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
    attach_download_indexes(job)
    return templates.TemplateResponse(request, "seedance_status.html", {"job": job})


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: int = Query(0)):
    job = get_job(job_id)
    return serve_job_file(
        job, index, as_attachment=bool(dl), default_media_type="video/mp4"
    )
