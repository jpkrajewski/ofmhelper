from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Form,
    UploadFile,
    File,
    BackgroundTasks,
    HTTPException,
)
from fastapi.responses import RedirectResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import (
    make_job_dir,
    build_ordered_paths,
    attach_download_indexes,
    serve_job_file,
)
from ofmhelpers.aigenproviders.kaiai.client import KieAIClient

router = APIRouter(prefix="/nanobanana", tags=["nanobanana"])

UPLOAD_ROOT = Path("uploads") / "nanobanana-refs"
ALLOWED_REF_ROOT = Path("uploads")


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


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(request, "nanobanana_form.html", {})


@router.post("/run")
async def run(
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

    job_dir = make_job_dir(UPLOAD_ROOT)
    image_input_paths = build_ordered_paths(
        job_dir, image_input_manifest, image_input, ALLOWED_REF_ROOT
    )

    params = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "output_format": output_format,
    }
    job_id = create_job("nanobanana", params)
    background_tasks.add_task(
        run_job,
        job_id,
        _run_nanobanana,
        {"api_key": api_key, **params, "image_input_paths": image_input_paths},
    )

    return RedirectResponse(url=f"/nanobanana/jobs/{job_id}", status_code=303)


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "nanobanana_form.html", {}, status_code=404
        )
    attach_download_indexes(job)
    return templates.TemplateResponse(request, "nanobanana_status.html", {"job": job})


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
    job = get_job(job_id)
    return serve_job_file(job, index)
