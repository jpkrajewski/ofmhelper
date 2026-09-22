from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
)

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.cache import enqueue
from ofmhelpers.log import get_logger
from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    asset_card,
    job_inputs,
    job_status_payload,
    register_generated_asset,
    resolve_reference_uploads,
    serve_job_file,
)
from ofmhelpers.web.schemas import ReferenceUploads
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job, set_job_preview
from ofmhelpers.web.templates_config import get_templates

logger = get_logger(__name__)
router = APIRouter(prefix="/wan3", tags=["wan3"])


def _run_wan3(
    job_id: str,
    api_key: str,
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    audio: bool,
    reference_image_paths: list[str],
    reference_video_paths: list[str],
    reference_audio_paths: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)

    kwargs = {
        "prompt": prompt,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "audio": audio,
    }

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
        out_path = client.generate_video_wan3(on_result_urls=_on_result_urls, **kwargs)
    except Exception:
        if not remote_urls:
            raise
        logger.warning(
            "local download failed, serving remote_url only: %s",
            remote_urls[0],
            exc_info=True,
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
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    # Declared before the defaulted fields because it has no default of its
    # own -- the dependency builds it from the six reference_* form fields.
    refs: Annotated[ReferenceUploads, Depends(ReferenceUploads.from_form)],
    resolution: Annotated[str, Form()] = "1080P",
    aspect_ratio: Annotated[str, Form()] = "adaptive",
    duration: Annotated[int, Form()] = 5,
    audio: Annotated[bool, Form()] = True,
):
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")

    reference_paths = resolve_reference_uploads(refs)

    params = {
        "prompt": prompt,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "audio": audio,
    }
    job_id = create_job(
        "wan3",
        {**params, **reference_paths},
        actor=request.session.get("role"),
    )
    enqueue(
        run_job,
        job_id,
        _run_wan3,
        {
            "job_id": job_id,
            "api_key": api_key,
            **params,
            "reference_image_paths": reference_paths["reference_images"],
            "reference_video_paths": reference_paths["reference_videos"],
            "reference_audio_paths": reference_paths["reference_audio"],
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
                f"/wan3/files/{job_id}",
                remote_url=f.get("remote_url"),
            )
            for idx, f in enumerate(job["result"])
        ]

    return get_templates().TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "job_inputs": job_inputs(job),
            "title": "Wan 3.0",
            "pending_message": "Generating video… this can take a few minutes.",
            "back_url": "/generate",
            "back_label": "generate another",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    return job_status_payload(get_job(job_id), "/wan3/files")


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int, dl: Annotated[int, Query()] = 0):
    job = get_job(job_id)
    return serve_job_file(job, index, as_attachment=bool(dl))
