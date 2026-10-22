"""
/uniqify: upload one video, get back N re-encoded variants of it as one zip,
each with its own speed / zoom / colour / grain / mirror / trim jitter
(`utils.uniqify`). Both roles; a VA only ever sees its own runs.
"""

import shutil
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.utils.uniqify import MetadataMode, Strength, generate_variants
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers import (
    UPLOADS_ROOT,
    VIDEO_KINDS,
    add_job_routes,
    job_assets,
    make_job_dir,
    require_upload_kind,
    safe_filename,
)
from ofmhelpers.web.schemas import Job, JobCard, JobQueued, ResultFile
from ofmhelpers.web.stores.jobs import create_job, list_jobs_page, run_job
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/uniqify", tags=["uniqify"])

UPLOAD_ROOT = UPLOADS_ROOT / "uniqify"

TASK = "uniqify"

FILES_PREFIX = f"{router.prefix}/files"

MAX_COPIES = settings.web.uniqify_max_copies

MAX_TRIM_S = settings.web.uniqify_max_trim_s


def _run_uniqify(
    job_dir: str,
    source: str,
    stem: str,
    copies: int,
    strength: str,
    mirror_pct: float,
    trim_secs: float,
    metadata: str,
) -> list[ResultFile]:
    directory = Path(job_dir)
    outputs = generate_variants(
        directory / source,
        directory,
        stem,
        copies,
        Strength(strength),
        mirror_pct,
        trim_secs,
        MetadataMode(metadata),
        settings.web.uniqify_encode_timeout_s,
    )
    archive = _zip_and_remove(outputs, directory / f"{stem}.zip")
    return [ResultFile(name=archive.name, path=str(archive))]


def _zip_and_remove(files: list[Path], dest: Path) -> Path:
    """Bundle the copies into one download, then drop the loose files -- the
    zip is the only thing a run hands back. Stored, not deflated: H.264 is
    already compressed, so deflating would only cost time."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, f.name)
    for f in files:
        f.unlink()
    return dest


def _gallery_card(job: Job) -> JobCard:
    return JobCard.from_job(job, *job_assets(job, FILES_PREFIX))


@router.get("")
def form(request: Request):
    recent, _total = list_jobs_page(
        {TASK}, 0, settings.web.gallery_limit, AuthMiddleware.actor(request)
    )
    return render(
        request,
        "uniqify.html",
        gallery=[_gallery_card(j) for j in recent],
        max_copies=MAX_COPIES,
        max_trim=MAX_TRIM_S,
        strengths=list(Strength),
        metadata_modes=list(MetadataMode),
    )


@router.post("/run")
def run(
    request: Request,
    file: Annotated[UploadFile, File()],
    copies: Annotated[int, Form(ge=1, le=MAX_COPIES)] = 5,
    strength: Annotated[Strength, Form()] = Strength.HARD,
    mirror_pct: Annotated[float, Form(ge=0, le=100)] = 0.0,
    trim_secs: Annotated[float, Form(ge=0, le=MAX_TRIM_S)] = 0.1,
    metadata: Annotated[MetadataMode, Form()] = MetadataMode.STRIP,
    output_name: Annotated[str, Form()] = "",
):
    source = require_upload_kind(file.filename, VIDEO_KINDS)
    stem = Path(safe_filename(output_name.strip() or source)).stem
    if not stem:
        raise HTTPException(status_code=400, detail="Invalid output name")

    job_dir = make_job_dir(UPLOAD_ROOT)
    # Prefixed so an output named like the upload can never overwrite it.
    saved = f"source_{source}"
    with (job_dir / saved).open("wb") as out:
        shutil.copyfileobj(file.file, out)

    params = {
        "source": saved,
        "stem": stem,
        "copies": copies,
        "strength": strength.value,
        "mirror_pct": mirror_pct,
        "trim_secs": trim_secs,
        "metadata": metadata.value,
    }
    job_id = create_job(
        TASK, {"dir": str(job_dir), **params}, actor=AuthMiddleware.actor(request)
    )
    enqueue(run_job, job_id, _run_uniqify, {"job_dir": str(job_dir), **params})
    return JobQueued(job_id=job_id)


add_job_routes(
    router,
    "Uniqify",
    lambda job: f"Rendering {job.params['copies']} copies…",
    back_url="/uniqify",
    back_label="uniqify another video",
)
