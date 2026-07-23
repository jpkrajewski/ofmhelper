from fastapi import (
    APIRouter,
    Request,
    Form,
    UploadFile,
    File,
    BackgroundTasks,
    HTTPException,
)
from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    build_ordered_paths,
    asset_card,
    serve_job_file,
    job_status_payload,
)
from ofmhelpers.aigenproviders.kaiai.client import KieAIClient

router = APIRouter(prefix="/nanobanana", tags=["nanobanana"])


def _run_nanobanana(
    api_key: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    output_format: str,
    image_input_paths: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)
    image_input_urls = [client.upload_local_file(p) for p in image_input_paths]
    out_path = client.generate_image_nbp(
        prompt=prompt,
        image_input=image_input_urls,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        output_format=output_format,
    )
    return [{"name": out_path.name, "path": str(out_path)}]


@router.post("/run")
async def run(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key: str = Form(...),
    prompt: str = Form(...),
    aspect_ratio: str = Form("1:1"),
    resolution: str = Form("1K"),
    output_format: str = Form("png"),
    image_input: list[UploadFile] = File(default=[]),
    image_input_manifest: str = Form("[]"),
):
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")

    image_input_paths = build_ordered_paths(
        image_input_manifest, image_input, ASSETS_ROOT
    )

    params = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "output_format": output_format,
    }
    # "image_input" matches the form's picker field name so /generate's
    # click-to-reuse can restore the refs as "existing" picker entries.
    job_id = create_job(
        "nanobanana",
        {**params, "image_input": image_input_paths},
        actor=request.session.get("role"),
    )
    background_tasks.add_task(
        run_job,
        job_id,
        _run_nanobanana,
        {"api_key": api_key, **params, "image_input_paths": image_input_paths},
    )

    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    assets = []
    if job.get("status") == "done":
        assets = [
            asset_card(f["name"], idx, f"/nanobanana/files/{job_id}")
            for idx, f in enumerate(job["result"])
        ]

    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "title": "Nano Banana Pro",
            "pending_message": "Generating image…",
            "back_url": "/generate",
            "back_label": "generate another",
        },
    )


@router.get("/jobs/{job_id}/status")
def job_status_json(job_id: str):
    return job_status_payload(get_job(job_id), "/nanobanana/files")


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
    job = get_job(job_id)
    return serve_job_file(job, index)
