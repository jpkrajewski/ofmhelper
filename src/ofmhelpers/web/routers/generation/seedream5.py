from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import (
    Seedream5Quality,
    Seedream5Variant,
    SeedreamOutputFormat,
)
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_job_routes,
    require_api_key,
    run_kie_generation,
    start_kie_job,
)
from ofmhelpers.web.routers.task_helpers import ASSETS_ROOT, build_ordered_paths

router = APIRouter(prefix="/seedream5", tags=["seedream5"])


def _run_seedream5(
    job_id: str,
    api_key: str,
    prompt: str,
    variant: str,
    aspect_ratio: str,
    quality: str,
    output_format: str,
    image_urls: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)
    uploaded = [client.upload_local_file(p) for p in image_urls]
    return run_kie_generation(
        job_id,
        "image",
        ASSETS_ROOT,
        lambda on_result_urls: client.generate_image_seedream5(
            prompt=prompt,
            variant=variant,
            image_urls=uploaded,
            aspect_ratio=aspect_ratio,
            quality=quality,
            output_format=output_format,
            on_result_urls=on_result_urls,
        ),
    )


@router.post("/run")
async def run(
    request: Request,
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    variant: Annotated[Seedream5Variant, Form()] = Seedream5Variant.PRO,
    aspect_ratio: Annotated[str, Form()] = "1:1",
    quality: Annotated[Seedream5Quality, Form()] = Seedream5Quality.BASIC,
    output_format: Annotated[SeedreamOutputFormat, Form()] = SeedreamOutputFormat.PNG,
    image_urls: Annotated[list[UploadFile] | None, File()] = None,
    image_urls_manifest: Annotated[str, Form()] = "[]",
):
    require_api_key(api_key)
    params = {
        "prompt": prompt,
        "variant": variant.value,
        "aspect_ratio": aspect_ratio,
        "quality": quality.value,
        "output_format": output_format.value,
    }
    image_paths = build_ordered_paths(
        image_urls_manifest, image_urls or [], ASSETS_ROOT
    )
    return start_kie_job(
        request,
        "seedream5",
        _run_seedream5,
        api_key,
        params,
        {"image_urls": image_paths},
    )


add_job_routes(router, "Seedream 5.0", "image")
