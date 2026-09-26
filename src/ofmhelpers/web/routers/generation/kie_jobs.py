"""The plumbing every kie.ai generation router shares, written once.

A kie.ai tool module is then just its `/run` form, the payload it hands to one
`KieAIClient.generate_*` method, and a call to `add_job_routes`.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import IMAGE_EXT, VIDEO_EXT
from ofmhelpers.cache import enqueue
from ofmhelpers.log import get_logger
from ofmhelpers.web.routers.task_helpers import (
    asset_card,
    job_inputs,
    job_status_payload,
    register_generated_asset,
    serve_job_file,
)
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job, set_job_preview
from ofmhelpers.web.templates_config import get_templates

logger = get_logger(__name__)

OnResultUrls = Callable[[list[str]], None]

_FALLBACK_NAME = {"image": f"image.{IMAGE_EXT}", "video": f"video.{VIDEO_EXT}"}
_PENDING_MESSAGE = {
    "image": "Generating image…",
    "video": "Generating video… this can take a few minutes.",
}


def require_api_key(api_key: str) -> None:
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")


def start_kie_job(
    request: Request,
    task: str,
    run_fn: Callable[..., list[dict]],
    api_key: str,
    params: dict,
    refs: dict[str, list[str]],
) -> dict:
    """Create the job and queue `run_fn` for the worker.

    `refs` is keyed by the form's picker field names: stored under those keys,
    /generate's click-to-reuse restores them as "existing" picker entries, and
    `run_fn` takes them as kwargs of the same names. api_key goes to the worker
    only -- never into the stored params the status page reads."""
    job_id = create_job(task, {**params, **refs}, actor=request.session.get("role"))
    enqueue(
        run_job,
        job_id,
        run_fn,
        {"job_id": job_id, "api_key": api_key, **params, **refs},
    )
    return {"job_id": job_id}


def upload_references(
    client: KieAIClient,
    reference_images: list[str],
    reference_videos: list[str],
    reference_audio: list[str],
) -> dict[str, list[str]]:
    """Upload the three reference pickers' files, as the reference_*_urls
    kwargs of an omni-reference `generate_*` method (empty lists left out)."""
    paths = {
        "reference_image_urls": reference_images,
        "reference_video_urls": reference_videos,
        "reference_audio_urls": reference_audio,
    }
    return {
        key: [client.upload_local_file(p) for p in value]
        for key, value in paths.items()
        if value
    }


def run_kie_generation(
    job_id: str,
    kind: str,
    assets_root: Path,
    generate: Callable[[OnResultUrls], Path],
) -> list[dict]:
    """Run one generation and shape it as a job result.

    kie.ai's hosted result is published as the job preview the moment the poll
    succeeds. If only the local download then fails, the job still succeeds on
    the hosted copy instead of losing a finished generation."""
    remote_urls: list[str] = []

    def on_result_urls(urls: list[str]) -> None:
        remote_urls.extend(urls)
        set_job_preview(job_id, {"remote_url": urls[0], "kind": kind})

    try:
        out_path = generate(on_result_urls)
    except Exception:
        if not remote_urls:
            raise
        logger.warning(
            "local download failed, serving remote_url only: %s",
            remote_urls[0],
            exc_info=True,
        )
        name = remote_urls[0].rsplit("/", 1)[-1].split("?")[0] or _FALLBACK_NAME[kind]
        return [{"name": name, "path": None, "remote_url": remote_urls[0]}]

    register_generated_asset(out_path, assets_root)
    return [
        {"name": out_path.name, "path": str(out_path), "remote_url": remote_urls[0]}
    ]


def add_job_routes(router: APIRouter, title: str, kind: str) -> None:
    """Register the status page, polling payload and file download under the
    router's own prefix -- the three GET endpoints of the standard tool shape."""
    files_prefix = f"{router.prefix}/files"

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
                    f"{files_prefix}/{job_id}",
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
                "title": title,
                "pending_message": _PENDING_MESSAGE[kind],
                "back_url": "/generate",
                "back_label": "generate another",
            },
        )

    @router.get("/jobs/{job_id}/status")
    def job_status_json(job_id: str):
        return job_status_payload(get_job(job_id), files_prefix)

    @router.get("/files/{job_id}/{index}")
    def download_file(job_id: str, index: int, dl: Annotated[int, Query()] = 0):
        return serve_job_file(get_job(job_id), index, as_attachment=bool(dl))
