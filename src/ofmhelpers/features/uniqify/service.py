"""What /uniqify does behind its routes (web/routers/video_tools/uniqify.py):
the page with the person's newest runs, and a run started from an upload. The
encode itself is `media.uniqify`; the worker function stays in the router (RQ
stores its path) and is handed in."""

import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel
lazy from starlette.datastructures import UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job, list_jobs_page
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.store import (
    VIDEO_KINDS,
    make_job_dir,
    require_upload_kind,
    safe_filename,
)
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.media.uniqify import MetadataMode, Strength, generate_variants
from ofmhelpers.schemas import Job, JobCard, JobQueued, ResultFile

TASK = "uniqify"


class Submitted(Protocol):
    """The route's form (`UniqifyForm`)."""

    @property
    def file(self) -> UploadFile: ...

    copies: int
    strength: Strength
    mirror_pct: float
    trim_secs: float
    metadata: MetadataMode
    output_name: str


class FormView(BaseModel):
    """/uniqify: each field is a name `uniqify.html` reads."""

    gallery: list[JobCard]
    max_copies: int
    max_trim: float
    strengths: list[Strength]
    metadata_modes: list[MetadataMode]


def _card(job: Job, files_prefix: str) -> JobCard:
    found = job_assets(job, files_prefix)
    return JobCard.from_job(job, found.assets, found.failed_sources)


def form_view(actor: str, files_prefix: str) -> FormView:
    recent = list_jobs_page({TASK}, 0, settings.web.gallery_limit, actor).jobs
    return FormView(
        gallery=[_card(job, files_prefix) for job in recent],
        max_copies=settings.web.uniqify_max_copies,
        max_trim=settings.web.uniqify_max_trim_s,
        strengths=list(Strength),
        metadata_modes=list(MetadataMode),
    )


def start(
    form: Submitted,
    actor: str,
    upload_root: Path,
    run: Callable[..., list[ResultFile]],
) -> JobQueued:
    """The upload saved in a fresh folder of `upload_root`, and its run
    (`run`: the router's worker function) queued."""
    source = require_upload_kind(form.file.filename, VIDEO_KINDS)
    stem = Path(safe_filename(form.output_name or source)).stem
    if not stem:
        msg = "Invalid output name"
        raise RefusedError(msg)

    job_dir = make_job_dir(upload_root)
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
    enqueue(run_job, job_id, run, {"job_dir": str(job_dir), **params})
    return JobQueued(job_id=job_id)


def run_uniqify(
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
