from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import Seedance2Model, SeedanceResolution
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_kie_job_routes,
    require_api_key,
    run_kie_generation,
    start_kie_job,
    upload_references,
)
from ofmhelpers.web.routers.task_helpers import (
    assets_root_for,
    resolve_reference_uploads,
)
from ofmhelpers.web.schemas import ReferenceUploads, ResultFile

router = APIRouter(prefix="/seedance", tags=["seedance"])


def _run_seedance(
    job_id: str,
    api_key: str,
    assets_root: str,
    prompt: str,
    model: str,
    resolution: str,
    aspect_ratio: str,
    duration: int,
    generate_audio: bool,
    reference_images: list[str],
    reference_videos: list[str],
    reference_audio: list[str],
) -> list[ResultFile]:
    client = KieAIClient.from_env(api_key=api_key)
    refs = upload_references(
        client, reference_images, reference_videos, reference_audio
    )
    return run_kie_generation(
        job_id,
        "video",
        assets_root,
        lambda on_result_urls: client.generate_video_seedance2(
            prompt=prompt,
            model=model,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            duration=duration,
            generate_audio=generate_audio,
            on_result_urls=on_result_urls,
            **refs,
        ),
    )


@router.post("/run")
def run(
    request: Request,
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    # Declared before the defaulted fields because it has no default of its
    # own -- the dependency builds it from the six reference_* form fields.
    refs: Annotated[ReferenceUploads, Depends(ReferenceUploads.from_form)],
    model: Annotated[Seedance2Model, Form()] = Seedance2Model.STANDARD,
    resolution: Annotated[SeedanceResolution, Form()] = SeedanceResolution.P720,
    aspect_ratio: Annotated[str, Form()] = "16:9",
    duration: Annotated[int, Form()] = 10,
    generate_audio: Annotated[bool, Form()] = False,
):
    require_api_key(api_key)
    params = {
        "prompt": prompt,
        "model": model.value,
        "resolution": resolution.value,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "generate_audio": generate_audio,
    }
    return start_kie_job(
        request,
        "seedance",
        _run_seedance,
        api_key,
        params,
        resolve_reference_uploads(refs, assets_root_for(request)),
    )


add_kie_job_routes(router, "Seedance 2.0", "video")
