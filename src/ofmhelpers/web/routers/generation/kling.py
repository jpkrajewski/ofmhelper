from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import Kling3Mode
from ofmhelpers.web.routers.generation.kie_jobs import (
    add_job_routes,
    require_api_key,
    run_kie_generation,
    start_kie_job,
)
from ofmhelpers.web.routers.task_helpers import ASSETS_ROOT, build_ordered_paths

router = APIRouter(prefix="/kling3", tags=["kling3"])


def _run_kling3(
    job_id: str,
    api_key: str,
    prompt: str,
    mode: str,
    aspect_ratio: str,
    duration: str,
    sound: bool,
    images: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)
    image_urls = [client.upload_local_file(p) for p in images]
    return run_kie_generation(
        job_id,
        "video",
        ASSETS_ROOT,
        lambda on_result_urls: client.generate_video_kling3(
            prompt=prompt,
            image_urls=image_urls or None,
            mode=mode,
            aspect_ratio=aspect_ratio,
            duration=duration,
            sound=sound,
            multi_shots=False,
            on_result_urls=on_result_urls,
        ),
    )


@router.post("/run")
async def run(
    request: Request,
    api_key: Annotated[str, Form()],
    prompt: Annotated[str, Form()],
    mode: Annotated[Kling3Mode, Form()] = Kling3Mode.PRO,
    aspect_ratio: Annotated[str, Form()] = "16:9",
    duration: Annotated[str, Form()] = "5",
    sound: Annotated[bool, Form()] = True,
    images: Annotated[list[UploadFile] | None, File()] = None,
    images_manifest: Annotated[str, Form()] = "[]",
):
    require_api_key(api_key)
    params = {
        "prompt": prompt,
        "mode": mode.value,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "sound": sound,
    }
    image_paths = build_ordered_paths(images_manifest, images or [], ASSETS_ROOT)
    return start_kie_job(
        request, "kling3", _run_kling3, api_key, params, {"images": image_paths}
    )


add_job_routes(router, "Kling 3.0", "video")
