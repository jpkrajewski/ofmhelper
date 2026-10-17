from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import IMAGE_EXT, ImageResolution
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_kie_job_routes,
    require_api_key,
    run_kie_generation,
    start_kie_job,
)
from ofmhelpers.web.routers.task_helpers import assets_root_for, build_ordered_paths
from ofmhelpers.web.schemas import ResultFile

router = APIRouter(prefix="/nanobanana", tags=["nanobanana"])


def _run_nanobanana(
    job_id: str,
    api_key: str,
    assets_root: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    output_format: str,
    image_input: list[str],
) -> list[ResultFile]:
    client = KieAIClient.from_env(api_key=api_key)
    image_input_urls = [client.upload_local_file(p) for p in image_input]
    return run_kie_generation(
        job_id,
        "image",
        assets_root,
        lambda on_result_urls: client.generate_image_nbp(
            prompt=prompt,
            image_input=image_input_urls,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            output_format=output_format,
            on_result_urls=on_result_urls,
        ),
    )


@router.post("/run")
def run(
    request: Request,
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    aspect_ratio: Annotated[str, Form()] = "1:1",
    resolution: Annotated[ImageResolution, Form()] = ImageResolution.K1,
    output_format: Annotated[str, Form()] = IMAGE_EXT,
    image_input: Annotated[list[UploadFile] | None, File()] = None,
    image_input_manifest: Annotated[str, Form()] = "[]",
):
    require_api_key(api_key)
    params = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution.value,
        "output_format": output_format,
    }
    image_paths = build_ordered_paths(
        image_input_manifest, image_input or [], assets_root_for(request)
    )
    return start_kie_job(
        request,
        "nanobanana",
        _run_nanobanana,
        api_key,
        params,
        {"image_input": image_paths},
    )


add_kie_job_routes(router, "Nano Banana Pro", "image")
