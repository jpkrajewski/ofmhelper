from fastapi import (
    APIRouter,
    Request,
    Form,
    UploadFile,
    File,
    HTTPException,
)
from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job, set_job_preview
from ofmhelpers.web.queue import enqueue
from ofmhelpers.web.routers.task_helpers import (
    ASSETS_ROOT,
    build_ordered_paths,
    asset_card,
    register_generated_asset,
    serve_job_file,
    job_status_payload,
    job_inputs,
)
from ofmhelpers.aigenproviders.kaiai.client import KieAIClient

router = APIRouter(prefix="/nanobanana", tags=["nanobanana"])


def _run_nanobanana(
    job_id: str,
    api_key: str,
    prompt: str,
    aspect_ratio: str,
    resolution: str,
    output_format: str,
    image_input_paths: list[str],
) -> list[dict]:
    client = KieAIClient.from_env(api_key=api_key)
    image_input_urls = [client.upload_local_file(p) for p in image_input_paths]

    # kie.ai's hosted result is live as soon as the poll succeeds -- publish
    # it as the job's preview right away so the UI can show it without
    # waiting for the (potentially slow) local download below.
    remote_urls: list[str] = []

    def _on_result_urls(urls: list[str]) -> None:
        remote_urls.extend(urls)
        set_job_preview(job_id, {"remote_url": urls[0], "kind": "image"})

    try:
        out_path = client.generate_image_nbp(
            prompt=prompt,
            image_input=image_input_urls,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            output_format=output_format,
            on_result_urls=_on_result_urls,
        )
    except Exception:
        if not remote_urls:
            raise  # never even got a hosted result -- a real failure
        # Generation itself succeeded and is still reachable at remote_urls;
        # only the local download failed. Never fail the job over that --
        # keep serving the hosted copy indefinitely instead of leaving the
        # asset in a broken state.
        print(
            f"[nanobanana] local download failed, serving remote_url only: "
            f"{remote_urls[0]}",
            flush=True,
        )
        name = remote_urls[0].rsplit("/", 1)[-1].split("?")[0] or "image.png"
        return [{"name": name, "path": None, "remote_url": remote_urls[0]}]

    register_generated_asset(out_path, ASSETS_ROOT)
    return [{"name": out_path.name, "path": str(out_path)}]


@router.post("/run")
async def run(
    request: Request,
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
    enqueue(
        run_job,
        job_id,
        _run_nanobanana,
        {
            "job_id": job_id,
            "api_key": api_key,
            **params,
            "image_input_paths": image_input_paths,
        },
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
            asset_card(
                f["name"],
                idx,
                f"/nanobanana/files/{job_id}",
                remote_url=f.get("remote_url"),
            )
            for idx, f in enumerate(job["result"])
        ]

    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "job_inputs": job_inputs(job),
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
