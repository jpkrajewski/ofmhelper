from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import ADAPTIVE_ASPECT_RATIO, Wan3Resolution
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_job_routes,
    require_api_key,
    run_kie_generation,
    start_kie_job,
    upload_references,
)
from ofmhelpers.web.routers.task_helpers import ASSETS_ROOT, resolve_reference_uploads
from ofmhelpers.web.schemas import ReferenceUploads

router = APIRouter(prefix="/wan3", tags=["wan3"])


def _run_wan3(
    job_id: str,
    api_key: str,
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    audio: bool,
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
        lambda on_result_urls: client.generate_video_wan3(
            prompt=prompt,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            audio=audio,
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
    resolution: Annotated[Wan3Resolution, Form()] = Wan3Resolution.P1080,
    aspect_ratio: Annotated[str, Form()] = ADAPTIVE_ASPECT_RATIO,
    duration: Annotated[int, Form()] = 5,
    audio: Annotated[bool, Form()] = True,
):
    require_api_key(api_key)
    params = {
        "prompt": prompt,
        "resolution": resolution.value,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "audio": audio,
    }
    return start_kie_job(
        request, "wan3", _run_wan3, api_key, params, resolve_reference_uploads(refs)
    )


add_job_routes(router, "Wan 3.0", "video")
