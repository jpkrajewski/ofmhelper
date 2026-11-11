"""/clean-images: pictures stripped of their metadata, then kept in the
person's part of the asset store."""

from pathlib import Path

lazy from fastapi import UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.db.stores.jobs import create_job
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
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.media.metadata_cleaner import clean_metadata
from ofmhelpers.schemas import JobQueued, ResultFile

TASK = "clean_images"


def upload_root() -> Path:
    return uploads_root() / "clean-images"


def run_clean(job_dir: str, assets_root: str) -> list[ResultFile]:
    directory = Path(job_dir)
    clean_metadata(directory)
    files = sorted(p for p in directory.iterdir() if p.info.is_file())
    # Adopt the stripped copies into the requester's part of the asset store
    # so they're directly reusable in the generation pickers -- cleaning an
    # image is usually the step right before generating with it.
    for p in files:
        register_generated_asset(p, Path(assets_root))
    return [ResultFile(name=p.name, path=str(p)) for p in files]


def start(files: list[UploadFile], actor: str, assets_root: Path) -> JobQueued:
    uploads = [convert_heic_upload(f) for f in files if f.filename]
    if not uploads:
        msg = "At least one image is required"
        raise RefusedError(msg)
    # Validate every name before writing any of them: the cleaner skips a
    # file it doesn't recognise rather than failing, which would otherwise
    # leave an unprocessed upload sitting in the job dir and listed in the
    # result (a .html there used to come back as a page on our own origin).
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
