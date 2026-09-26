from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import (
    ADAPTIVE_ASPECT_RATIO,
    SeedanceResolution,
)
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_job_routes,
    require_api_key,
    run_kie_generation,
    start_kie_job,
    upload_references,
)
from ofmhelpers.web.routers.task_helpers import ASSETS_ROOT, resolve_reference_uploads
from ofmhelpers.web.schemas import ReferenceUploads

router = APIRouter(prefix="/seedance25", tags=["seedance25"])


def _run_seedance25(
    job_id: str,
    api_key: str,
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    generate_audio: bool,
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
        lambda on_result_urls: client.generate_video_seedance25(
            prompt=prompt,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            generate_audio=generate_audio,
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
    resolution: Annotated[SeedanceResolution, Form()] = SeedanceResolution.P720,
    aspect_ratio: Annotated[str, Form()] = ADAPTIVE_ASPECT_RATIO,
    duration: Annotated[int, Form()] = 5,
    generate_audio: Annotated[bool, Form()] = False,
):
    require_api_key(api_key)
    params = {
        "prompt": prompt,
        "resolution": resolution.value,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "generate_audio": generate_audio,
    }
    return start_kie_job(
        request,
        "seedance25",
        _run_seedance25,
        api_key,
        params,
        resolve_reference_uploads(refs),
    )


add_job_routes(router, "Seedance 2.5", "video")
