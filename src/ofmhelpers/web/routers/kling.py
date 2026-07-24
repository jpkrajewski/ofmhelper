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

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job, set_job_preview
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

router = APIRouter(prefix="/kling3", tags=["kling3"])


def _run_kling3(
    job_id: str,
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

    remote_urls: list[str] = []

    def _on_result_urls(urls: list[str]) -> None:
        remote_urls.extend(urls)
        set_job_preview(job_id, {"remote_url": urls[0], "kind": "video"})

    try:
        out_path = client.generate_video_kling3(
            prompt=prompt,
            image_urls=image_urls or None,
            mode=mode,
            aspect_ratio=aspect_ratio,
            duration=duration,
            sound=sound,
            multi_shots=False,
            on_result_urls=_on_result_urls,
        )
    except Exception:
        if not remote_urls:
            raise
        print(
            f"[kling3] local download failed, serving remote_url only: "
            f"{remote_urls[0]}",
            flush=True,
        )
        name = remote_urls[0].rsplit("/", 1)[-1].split("?")[0] or "video.mp4"
        return [{"name": name, "path": None, "remote_url": remote_urls[0]}]

    register_generated_asset(out_path, ASSETS_ROOT)
    return [{"name": out_path.name, "path": str(out_path)}]


@router.post("/run")
async def run(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key: str = Form(...),
    prompt: str = Form(...),
    mode: str = Form("pro"),
    aspect_ratio: str = Form("16:9"),
    duration: str = Form("5"),
    sound: bool = Form(True),
    images: list[UploadFile] = File(default=[]),
    images_manifest: str = Form("[]"),
):
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")

    image_paths = build_ordered_paths(images_manifest, images, ASSETS_ROOT)

    params = {
        "prompt": prompt,
        "mode": mode,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "sound": sound,
    }
    # "images" matches the form's picker field name so /generate's
    # click-to-reuse can restore the refs as "existing" picker entries.
    job_id = create_job(
        "kling3", {**params, "images": image_paths}, actor=request.session.get("role")
    )
    background_tasks.add_task(
        run_job,
        job_id,
        _run_kling3,
        {"job_id": job_id, "api_key": api_key, **params, "image_paths": image_paths},
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
                f"/kling3/files/{job_id}",
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
            "title": "Kling 3.0",
            "pending_message": "Generating video… this can take a few minutes.",
            "back_url": "/generate",
            "back_label": "generate another",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    return job_status_payload(get_job(job_id), "/kling3/files")


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: int = Query(0)):
    job = get_job(job_id)
    return serve_job_file(
        job, index, as_attachment=bool(dl), default_media_type="video/mp4"
    )
