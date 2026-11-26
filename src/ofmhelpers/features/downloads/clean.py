# Copyright (C) 2026 jpkrajewski

from pathlib import Path
lazy from collections.abc import Sequence

from ofmhelpers.cache import enqueue
from ofmhelpers.db.jobs.schema import ResultFile
from ofmhelpers.db.jobs.store import create_job
from ofmhelpers.features.assets.store import (
    make_job_dir,
    register_generated_asset,
    save_upload,
    uploads_root,
)
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.jobs.schema import JobQueued
from ofmhelpers.media.metadata_cleaner import clean_metadata
lazy from ofmhelpers.features.assets.models import ImageUpload

TASK = "clean_images"


def upload_root() -> Path:
    return uploads_root() / "clean-images"


def run_clean(
    job_dir: str,
    assets_root: str,
    files: list[str] | None = None,
) -> list[ResultFile]:
    outputs = clean_metadata(Path(job_dir), None if files is None else set(files))
    for p in outputs:
        register_generated_asset(p, Path(assets_root))
    return [ResultFile(name=p.name, path=str(p)) for p in outputs]


def start(files: Sequence[ImageUpload], actor: str, assets_root: Path) -> JobQueued:
    job_dir = make_job_dir(upload_root())
    saved_names = [Path(save_upload(job_dir, f)).name for f in files]
    job_id = create_job(TASK, {"dir": str(job_dir), "files": saved_names}, actor=actor)
    enqueue(
        run_job,
        (
            job_id,
            run_clean,
            {
                "job_dir": str(job_dir),
                "assets_root": str(assets_root),
                "files": saved_names,
            },
        ),
    )
    return JobQueued(job_id=job_id)
