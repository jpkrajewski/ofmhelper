from pathlib import Path

lazy from fastapi import UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.db.jobs.schema import ResultFile
from ofmhelpers.db.jobs.store import create_job
from ofmhelpers.features.assets.store import (
    IMAGE_KINDS,
    convert_heic_upload,
    make_job_dir,
    register_generated_asset,
    require_upload_kind,
    save_upload,
    uploads_root,
)
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.jobs.schema import JobQueued
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.media.metadata_cleaner import clean_metadata

TASK = "clean_images"


def upload_root() -> Path:
    return uploads_root() / "clean-images"


def run_clean(job_dir: str, assets_root: str) -> list[ResultFile]:
    directory = Path(job_dir)
    clean_metadata(directory)
    files = sorted(p for p in directory.iterdir() if p.info.is_file())
    for p in files:
        register_generated_asset(p, Path(assets_root))
    return [ResultFile(name=p.name, path=str(p)) for p in files]


def start(files: list[UploadFile], actor: str, assets_root: Path) -> JobQueued:
    uploads = [convert_heic_upload(f) for f in files if f.filename]
    if not uploads:
        msg = "At least one image is required"
        raise RefusedError(msg)
    for f in uploads:
        require_upload_kind(f.filename, IMAGE_KINDS)
    job_dir = make_job_dir(upload_root())
    saved_names = [Path(save_upload(job_dir, f)).name for f in uploads]
    job_id = create_job(TASK, {"dir": str(job_dir), "files": saved_names}, actor=actor)
    enqueue(
        run_job,
        job_id,
        run_clean,
        {"job_dir": str(job_dir), "assets_root": str(assets_root)},
    )
    return JobQueued(job_id=job_id)
