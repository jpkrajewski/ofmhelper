"""The plumbing every kie.ai generation router shares, written once.

A kie.ai tool module is then just its `/run` form, the payload it hands to one
`KieAIClient.generate_*` method, and a call to `add_kie_job_routes`.
"""

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import IMAGE_EXT, VIDEO_EXT
from ofmhelpers.cache import enqueue
from ofmhelpers.log import get_logger
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    register_generated_asset,
)
from ofmhelpers.web.schemas import JobPreview, JobQueued, ResultFile
from ofmhelpers.web.stores.jobs import create_job, run_job, set_job_preview

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
    run_fn: Callable[..., list[ResultFile]],
    api_key: str,
    params: dict,
    refs: dict[str, list[str]],
) -> JobQueued:
    """Create the job and queue `run_fn` for the worker.

    `refs` is keyed by the form's picker field names: stored under those keys,
    /generate's click-to-reuse restores them as "existing" picker entries, and
    `run_fn` takes them as kwargs of the same names. api_key goes to the worker
    only -- never into the stored params the status page reads, and so does
    `assets_root`, where `run_fn` registers its output (the requester's part
    of the asset store)."""
    job_id = create_job(task, {**params, **refs}, actor=request.session.get("role"))
    enqueue(
        run_job,
        job_id,
        run_fn,
        {
            "job_id": job_id,
            "api_key": api_key,
            "assets_root": str(assets_root_for(request)),
            **params,
            **refs,
        },
    )
    return JobQueued(job_id=job_id)


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
    assets_root: str,
    generate: Callable[[OnResultUrls], Path],
) -> list[ResultFile]:
    """Run one generation and shape it as a job result.

    kie.ai's hosted result is published as the job preview the moment the poll
    succeeds. If only the local download then fails, the job still succeeds on
    the hosted copy instead of losing a finished generation."""
    remote_urls: list[str] = []

    def on_result_urls(urls: list[str]) -> None:
        remote_urls.extend(urls)
        set_job_preview(job_id, JobPreview(remote_url=urls[0], kind=kind))

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
        return [ResultFile(name=name, path=None, remote_url=remote_urls[0])]

    register_generated_asset(out_path, Path(assets_root))
    return [
        ResultFile(name=out_path.name, path=str(out_path), remote_url=remote_urls[0])
    ]


def add_kie_job_routes(
    router: APIRouter,
    title: str,
    kind: str,
    back_url: str = "/generate",
    back_label: str = "generate another",
) -> None:
    """The standard job routes (`task_helpers.add_job_routes`), worded for a
    kie.ai image or video generation and showing the job's inputs."""
    message = _PENDING_MESSAGE[kind]
    add_job_routes(
        router,
        title,
        lambda _job: message,
        back_url=back_url,
        back_label=back_label,
        show_inputs=True,
    )
