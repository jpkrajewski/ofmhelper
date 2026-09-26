from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import ImageBackground, ImageResolution
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_job_routes,
    require_api_key,
    run_kie_generation,
    start_kie_job,
)
from ofmhelpers.web.routers.task_helpers import ASSETS_ROOT, build_ordered_paths

router = APIRouter(prefix="/gpt-image", tags=["gpt_image"])


def _run_gpt_image(
    job_id: str,
    api_key: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    background: str,
    input_urls: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)
    uploaded = [client.upload_local_file(p) for p in input_urls]
    return run_kie_generation(
        job_id,
        "image",
        ASSETS_ROOT,
        lambda on_result_urls: client.generate_image_gpt25_flare(
            prompt=prompt,
            input_urls=uploaded,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            background=background,
            on_result_urls=on_result_urls,
        ),
    )


@router.post("/run")
async def run(
    request: Request,
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    aspect_ratio: Annotated[str, Form()] = "auto",
    resolution: Annotated[ImageResolution, Form()] = ImageResolution.K1,
    background: Annotated[ImageBackground, Form()] = ImageBackground.AUTO,
    input_urls: Annotated[list[UploadFile] | None, File()] = None,
    input_urls_manifest: Annotated[str, Form()] = "[]",
):
    require_api_key(api_key)
    params = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution.value,
        "background": background.value,
    }
    input_paths = build_ordered_paths(
        input_urls_manifest, input_urls or [], ASSETS_ROOT
    )
    if not input_paths:
        raise HTTPException(status_code=400, detail="Add at least one input image")
    return start_kie_job(
        request,
        "gpt_image",
        _run_gpt_image,
        api_key,
        params,
        {"input_urls": input_paths},
    )


add_job_routes(router, "GPT Image 2.5 Flare", "image")
