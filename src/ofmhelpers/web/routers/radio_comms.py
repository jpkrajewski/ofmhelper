"""
ofmhelpers/web/routers/radio_comms.py
"""

import shutil
import uuid
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
from fastapi.responses import RedirectResponse, FileResponse

from ofmhelpers.utils.radio_comms_fx import PRESETS, process_file, generate_variations
from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job

router = APIRouter(prefix="/helpers/radio-comms", tags=["radio-comms"])

UPLOAD_ROOT = Path("uploads") / "radio-comms"


def _run_radio_comms(
    job_dir: str,
    input_paths: list[str],
    preset: str,
    mode: str,  # "single" or "variations"
    count: int,
    jitter_amount: float,
    seed: int | None,
) -> list[dict]:
    out_dir = Path(job_dir) / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[Path] = []
    for in_path in input_paths:
        in_path = Path(in_path)
        if mode == "variations":
            paths = generate_variations(
                str(in_path),
                str(out_dir),
                preset,
                count=count,
                jitter_amount=jitter_amount,
                base_seed=seed,
            )
            results.extend(Path(p) for p in paths)
        else:
            out_path = out_dir / f"{in_path.stem}_{preset}.wav"
            process_file(str(in_path), str(out_path), preset, seed=seed)
            results.append(out_path)

    return [{"name": p.name, "path": str(p)} for p in results]


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(
        request,
        "radio_comms_form.html",
        {"presets": list(PRESETS.keys())},
    )


@router.post("/run")
async def run(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    preset: str = Form("cod_clean"),
    mode: str = Form("single"),  # "single" or "variations"
    count: int = Form(10),
    jitter_amount: float = Form(0.18),
    seed: int | None = Form(None),
):
    if preset not in PRESETS:
        raise HTTPException(status_code=400, detail=f"Unknown preset '{preset}'")

    job_dir = UPLOAD_ROOT / uuid.uuid4().hex[:8]
    job_dir.mkdir(parents=True, exist_ok=True)

    input_paths = []
    for f in files:
        dest = job_dir / f.filename
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        input_paths.append(str(dest))

    params = {
        "preset": preset,
        "mode": mode,
        "count": count,
        "jitter_amount": jitter_amount,
        "seed": seed,
    }
    job_id = create_job("radio_comms", params)
    background_tasks.add_task(
        run_job,
        job_id,
        _run_radio_comms,
        {
            "job_dir": str(job_dir),
            "input_paths": input_paths,
            **params,
        },
    )

    return RedirectResponse(url=f"/helpers/radio-comms/jobs/{job_id}", status_code=303)


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "radio_comms_form.html", {}, status_code=404
        )

    if job.get("status") == "done":
        for idx, f in enumerate(job["result"]):
            f["index"] = idx

    return templates.TemplateResponse(request, "radio_comms_status.html", {"job": job})


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
    job = get_job(job_id)
    if job is None or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    files = job["result"]
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(files[index]["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")

    return FileResponse(path, filename=path.name, media_type="audio/wav")
