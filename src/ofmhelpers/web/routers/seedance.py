from enum import Enum

from fastapi import (
    APIRouter,
    Request,
    Form,
    UploadFile,
    File,
    HTTPException,
    Query,
)
from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job, set_job_preview
from ofmhelpers.web.queue import enqueue
from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    build_ordered_paths,
    asset_card,
    register_generated_asset,
    serve_job_file,
    job_status_payload,
    job_inputs,
)
from ofmhelpers.aigenproviders.kaiai.client import KieAIClient

router = APIRouter(prefix="/seedance", tags=["seedance"])


class SeedanceModel(str, Enum):
    standard = "bytedance/seedance-2"
    fast = "bytedance/seedance-2-fast"
    mini = "bytedance/seedance-2-mini"


def _run_seedance(
    job_id: str,
    api_key: str,
    prompt: str,
    model: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    generate_audio: bool,
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

    remote_urls: list[str] = []

    def _on_result_urls(urls: list[str]) -> None:
        remote_urls.extend(urls)
        set_job_preview(job_id, {"remote_url": urls[0], "kind": "video"})

    try:
        out_path = client.generate_video_seedance2(
            on_result_urls=_on_result_urls, **kwargs
        )
    except Exception:
        if not remote_urls:
            raise
        print(
            f"[seedance] local download failed, serving remote_url only: "
            f"{remote_urls[0]}",
            flush=True,
        )
        name = remote_urls[0].rsplit("/", 1)[-1].split("?")[0] or "video.mp4"
        return [{"name": name, "path": None, "remote_url": remote_urls[0]}]

    register_generated_asset(out_path, ASSETS_ROOT)
    return [
        {"name": out_path.name, "path": str(out_path), "remote_url": remote_urls[0]}
    ]


@router.post("/run")
async def run(
    request: Request,
    api_key: str = Form(...),
    prompt: str = Form(...),
    model: SeedanceModel = Form(SeedanceModel.standard),
    resolution: str = Form("720p"),
    aspect_ratio: str = Form("16:9"),
    duration: int = Form(10),
    generate_audio: bool = Form(False),
    reference_images: list[UploadFile] = File(default=[]),
    reference_images_manifest: str = Form("[]"),
    reference_videos: list[UploadFile] = File(default=[]),
    reference_videos_manifest: str = Form("[]"),
    reference_audio: list[UploadFile] = File(default=[]),
    reference_audio_manifest: str = Form("[]"),
):
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")

    # reference_* are ordered, reusable multi-file lists -- these go through
    # the manifest so previously-uploaded refs get reused by path instead of
    # re-uploaded.
    reference_image_paths = build_ordered_paths(
        reference_images_manifest, reference_images, ASSETS_ROOT
    )
    reference_video_paths = build_ordered_paths(
        reference_videos_manifest, reference_videos, ASSETS_ROOT
    )
    reference_audio_paths = build_ordered_paths(
        reference_audio_manifest, reference_audio, ASSETS_ROOT
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
    }
    # Stored params also carry the reference paths, keyed by the form's
    # picker field names -- that's what lets /generate's click-to-reuse
    # restore them as "existing" picker entries (with previews) later.
    job_id = create_job(
        "seedance",
        {
            **params,
            "reference_images": reference_image_paths,
            "reference_videos": reference_video_paths,
            "reference_audio": reference_audio_paths,
        },
        actor=request.session.get("role"),
    )
    enqueue(
        run_job,
        job_id,
        _run_seedance,
        {
            "job_id": job_id,
            "api_key": api_key,
            **params,
            "reference_image_paths": reference_image_paths,
            "reference_video_paths": reference_video_paths,
            "reference_audio_paths": reference_audio_paths,
        },
    )

    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    assets = []
    if job.get("status") == "done":
        assets = [
            asset_card(
                f["name"],
                idx,
                f"/seedance/files/{job_id}",
                remote_url=f.get("remote_url"),
            )
            for idx, f in enumerate(job["result"])
        ]

    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "job_inputs": job_inputs(job),
            "title": "Seedance",
            "pending_message": "Generating video… this can take a few minutes.",
            "back_url": "/generate",
            "back_label": "generate another",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    return job_status_payload(get_job(job_id), "/seedance/files")


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: int = Query(0)):
    job = get_job(job_id)
    return serve_job_file(
        job, index, as_attachment=bool(dl), default_media_type="video/mp4"
    )
