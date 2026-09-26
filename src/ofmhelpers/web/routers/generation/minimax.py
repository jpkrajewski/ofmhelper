from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import MinimaxH3Resolution
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_job_routes,
    require_api_key,
    run_kie_generation,
    start_kie_job,
    upload_references,
)
from ofmhelpers.web.routers.task_helpers import ASSETS_ROOT, resolve_reference_uploads
from ofmhelpers.web.schemas import ReferenceUploads

router = APIRouter(prefix="/minimax-h3", tags=["minimax_h3"])


def _run_minimax_h3(
    job_id: str,
    api_key: str,
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    reference_images: list[str],
    reference_videos: list[str],
    reference_audio: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)
    refs = upload_references(
        client, reference_images, reference_videos, reference_audio
    )
    return run_kie_generation(
        job_id,
        "video",
        ASSETS_ROOT,
        lambda on_result_urls: client.generate_video_minimax_h3(
            prompt=prompt,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            on_result_urls=on_result_urls,
            **refs,
        ),
    )


@router.post("/run")
async def run(
    request: Request,
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    refs: Annotated[ReferenceUploads, Depends(ReferenceUploads.from_form)],
    resolution: Annotated[MinimaxH3Resolution, Form()] = MinimaxH3Resolution.K2,
    aspect_ratio: Annotated[str, Form()] = "16:9",
    duration: Annotated[int, Form()] = 6,
):
    require_api_key(api_key)
    params = {
        "prompt": prompt,
        "resolution": resolution.value,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
    }
    return start_kie_job(
        request,
        "minimax_h3",
        _run_minimax_h3,
        api_key,
        params,
        resolve_reference_uploads(refs),
    )


add_job_routes(router, "MiniMax H3", "video")
