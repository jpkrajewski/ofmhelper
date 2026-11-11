"""/character-sheet: several shots of one character (typically 9:16) back as
one lossless PNG, side by side in upload order; the sheet lands in the
person's part of the asset store, ready for the reuse picker."""

import shutil
from pathlib import Path
lazy from collections.abc import Sequence

lazy from starlette.datastructures import UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job, list_jobs_page
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.store import (
    IMAGE_KINDS,
    convert_heic_upload,
    make_job_dir,
    register_generated_asset,
    require_upload_kind,
    uploads_root,
)
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.media.character_sheet import combine_side_by_side
from ofmhelpers.schemas import Job, JobCard, JobQueued, ResultFile

TASK = "character_sheet"
PREFIX = "/character-sheet"
FILES_PREFIX = f"{PREFIX}/files"
SHEET_NAME = "character_sheet.png"
# One image is not a sheet.
MIN_IMAGES = 2


def upload_root() -> Path:
    return uploads_root() / "character-sheet"


def run_combine(
    job_dir: str, files: list[str], assets_root: str | None = None
) -> list[ResultFile]:
    directory = Path(job_dir)
    sheet = combine_side_by_side(
        [directory / name for name in files], directory / SHEET_NAME
    )
    # Straight into the reuse picker: a character sheet is a reference image
    # for the generation tools.
    register_generated_asset(sheet, Path(assets_root) if assets_root else None)
    return [ResultFile(name=sheet.name, path=str(sheet))]


def _card(job: Job) -> JobCard:
    found = job_assets(job, FILES_PREFIX)
    return JobCard.from_job(job, found.assets, found.failed_sources)


def cards(actor: str) -> list[JobCard]:
    """The person's newest sheets."""
    recent = list_jobs_page({TASK}, 0, settings.web.gallery_limit, actor).jobs
    return [_card(job) for job in recent]


def _saved(uploads: list[UploadFile], names: list[str], job_dir: Path) -> list[str]:
    """Numbered, not saved under the client's name: iOS names every shared
    photo "image.jpg", and the number is also what keeps upload order."""
    saved = []
    for i, (upload, name) in enumerate(zip(uploads, names, strict=True)):
        dest = job_dir / f"{i:02d}_{name}"
        with dest.open("wb") as out:
            shutil.copyfileobj(upload.file, out)
        saved.append(dest.name)
    return saved


def start(files: Sequence[UploadFile], actor: str, assets_root: Path) -> JobQueued:
    uploads = [convert_heic_upload(f) for f in files if f.filename]
    max_images = settings.web.character_sheet_max_images
    if not MIN_IMAGES <= len(uploads) <= max_images:
        msg = f"Upload between {MIN_IMAGES} and {max_images} images"
        raise RefusedError(msg)
    names = [require_upload_kind(f.filename, IMAGE_KINDS) for f in uploads]
    job_dir = make_job_dir(upload_root())
    saved = _saved(uploads, names, job_dir)
    job_id = create_job(TASK, {"dir": str(job_dir), "files": saved}, actor=actor)
    enqueue(
        run_job,
        job_id,
        run_combine,
        {"job_dir": str(job_dir), "files": saved, "assets_root": str(assets_root)},
    )
    return JobQueued(job_id=job_id)
