# Copyright (C) 2026 jpkrajewski

import shutil
from pathlib import Path
lazy from collections.abc import Sequence

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.jobs.schema import Job, ResultFile
from ofmhelpers.db.jobs.store import create_job, list_jobs_page
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.store import (
    make_job_dir,
    register_generated_asset,
    uploads_root,
)
from ofmhelpers.features.jobs.models import JobCard
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.jobs.schema import JobQueued
from ofmhelpers.media.character_sheet import combine_side_by_side
lazy from ofmhelpers.features.assets.models import ImageUpload

TASK = "character_sheet"
PREFIX = "/character-sheet"
FILES_PREFIX = f"{PREFIX}/files"
SHEET_NAME = "character_sheet.png"


def upload_root() -> Path:
    return uploads_root() / "character-sheet"


def run_combine(
    job_dir: str,
    files: list[str],
    assets_root: str | None = None,
) -> list[ResultFile]:
    directory = Path(job_dir)
    sheet = combine_side_by_side(
        [directory / name for name in files],
        directory / SHEET_NAME,
    )
    register_generated_asset(sheet, Path(assets_root) if assets_root else None)
    return [ResultFile(name=sheet.name, path=str(sheet))]


def _card(job: Job) -> JobCard:
    found = job_assets(job, FILES_PREFIX)
    return JobCard.from_job(job, found.assets, found.failed_sources)


def cards(actor: str) -> list[JobCard]:
    recent = list_jobs_page({TASK}, 0, settings.web.gallery_limit, actor).jobs
    return [_card(job) for job in recent]


def _saved(uploads: Sequence[ImageUpload], job_dir: Path) -> list[str]:
    saved = []
    for i, upload in enumerate(uploads):
        dest = job_dir / f"{i:02d}_{upload.filename}"
        with dest.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
        saved.append(dest.name)
    return saved


def start(files: Sequence[ImageUpload], actor: str, assets_root: Path) -> JobQueued:
    job_dir = make_job_dir(upload_root())
    saved = _saved(files, job_dir)
    job_id = create_job(TASK, {"dir": str(job_dir), "files": saved}, actor=actor)
    enqueue(
        run_job,
        (
            job_id,
            run_combine,
            {"job_dir": str(job_dir), "files": saved, "assets_root": str(assets_root)},
        ),
    )
    return JobQueued(job_id=job_id)
