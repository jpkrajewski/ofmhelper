"""
/uniqify: upload one video, get back N re-encoded variants of it as one zip,
each with its own speed / zoom / colour / grain / mirror / trim jitter
(`utils.uniqify`). Both roles; a VA only ever sees its own runs.
"""

import shutil
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import Field

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job, list_jobs_page, run_job
from ofmhelpers.media.uniqify import MetadataMode, Strength, generate_variants
from ofmhelpers.schemas import Job, JobCard, JobQueued, ResultFile
from ofmhelpers.web.dependencies import Actor
from ofmhelpers.web.forms import FormModel, Stripped
from ofmhelpers.web.routers.task_helpers import (
    UPLOADS_ROOT,
    VIDEO_KINDS,
    add_job_routes,
    job_assets,
    make_job_dir,
    require_upload_kind,
    safe_filename,
)
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/uniqify", tags=["uniqify"])

UPLOAD_ROOT = UPLOADS_ROOT / "uniqify"

TASK = "uniqify"

FILES_PREFIX = f"{router.prefix}/files"

MAX_COPIES = settings.web.uniqify_max_copies

MAX_TRIM_S = settings.web.uniqify_max_trim_s


class UniqifyForm(FormModel):
    file: UploadFile
    copies: int = Field(5, ge=1, le=MAX_COPIES)
    strength: Strength = Strength.HARD
    mirror_pct: float = Field(0.0, ge=0, le=100)
    trim_secs: float = Field(0.1, ge=0, le=MAX_TRIM_S)
    metadata: MetadataMode = MetadataMode.STRIP
    output_name: Stripped = ""


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
def form(request: Request, actor: Actor) -> Response:
    recent, _total = list_jobs_page({TASK}, 0, settings.web.gallery_limit, actor)
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
def run(form: Annotated[UniqifyForm, Form()], actor: Actor) -> JobQueued:
    source = require_upload_kind(form.file.filename, VIDEO_KINDS)
    stem = Path(safe_filename(form.output_name or source)).stem
    if not stem:
        raise HTTPException(status_code=400, detail="Invalid output name")

    job_dir = make_job_dir(UPLOAD_ROOT)
    # Prefixed so an output named like the upload can never overwrite it.
    saved = f"source_{source}"
    with (job_dir / saved).open("wb") as out:
        shutil.copyfileobj(form.file.file, out)

    params = {
        "source": saved,
        "stem": stem,
        "copies": form.copies,
        "strength": form.strength.value,
        "mirror_pct": form.mirror_pct,
        "trim_secs": form.trim_secs,
        "metadata": form.metadata.value,
    }
    job_id = create_job(TASK, {"dir": str(job_dir), **params}, actor=actor)
    enqueue(run_job, job_id, _run_uniqify, {"job_dir": str(job_dir), **params})
    return JobQueued(job_id=job_id)


add_job_routes(
    router,
    "Uniqify",
    lambda job: f"Rendering {job.params['copies']} copies…",
    back_url="/uniqify",
    back_label="uniqify another video",
)
