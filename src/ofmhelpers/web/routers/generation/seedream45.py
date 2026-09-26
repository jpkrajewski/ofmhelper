from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import Seedream45Quality
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_job_routes,
    require_api_key,
    run_kie_generation,
    start_kie_job,
)
from ofmhelpers.web.routers.task_helpers import ASSETS_ROOT, build_ordered_paths

router = APIRouter(prefix="/seedream45", tags=["seedream45"])


def _run_seedream45(
    job_id: str,
    api_key: str,
    prompt: str,
    aspect_ratio: str,
    quality: str,
    image_urls: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)
    uploaded = [client.upload_local_file(p) for p in image_urls]
    return run_kie_generation(
        job_id,
        "image",
        ASSETS_ROOT,
        lambda on_result_urls: client.generate_image_seedream45(
            prompt=prompt,
            image_urls=uploaded,
            aspect_ratio=aspect_ratio,
            quality=quality,
            on_result_urls=on_result_urls,
        ),
    )


@router.post("/run")
async def run(
    request: Request,
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    aspect_ratio: Annotated[str, Form()] = "1:1",
    quality: Annotated[Seedream45Quality, Form()] = Seedream45Quality.BASIC,
    image_urls: Annotated[list[UploadFile] | None, File()] = None,
    image_urls_manifest: Annotated[str, Form()] = "[]",
):
    require_api_key(api_key)
    params = {"prompt": prompt, "aspect_ratio": aspect_ratio, "quality": quality.value}
    image_paths = build_ordered_paths(
        image_urls_manifest, image_urls or [], ASSETS_ROOT
    )
    return start_kie_job(
        request,
        "seedream45",
        _run_seedream45,
        api_key,
        params,
        {"image_urls": image_paths},
    )


add_job_routes(router, "Seedream 4.5", "image")
