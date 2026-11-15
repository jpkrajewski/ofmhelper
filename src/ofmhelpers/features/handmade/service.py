"""What /handmade does behind its routes (web/routers/inventory/handmade.py):
a VA's handmade reels, uploaded here and kept on the server. Owner
2026-11-15: "new page where va can upload their reels that were handmade",
then "i want to see what is in handmade by who and i choose where to upload
they dont automaticly upload to folder let it stay on the server".

One job per reel, so each is its own card: the file saved in a folder of its
own (`uploads/handmade/<dir>`), the job's result is that file (its gallery
preview made like any result). Nothing goes to Drive by itself: the owner
(their VAs' reels, "Done by VA", the VAs filter) picks Upload to Drive on a
card and the account folder on /drive, the same upload as /clone's
(`features/drive/uploads.py`, `drive_uploads`); the card then says where it
is. A VA uploads, never sends to Drive (`Action.UPLOAD_HANDMADE`,
`Action.DRIVE_UPLOAD`)."""

from pathlib import Path

lazy from fastapi import UploadFile

from ofmhelpers.config import settings
from ofmhelpers.db.stores import drive_uploads as upload_store
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.store import (
    VIDEO_KINDS,
    make_job_dir,
    require_upload_kind,
    save_upload,
    uploads_root,
)
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.schemas import CloneCard, DriveUpload, Job, JobsQueued, ResultFile

TASK = "handmade_upload"
LABEL = "Handmade"
PREFIX = "/handmade"
FILES_PREFIX = f"{PREFIX}/files"
_MB = 1024 * 1024


def upload_root() -> Path:
    return uploads_root() / "handmade"


def _checked(files: list[UploadFile]) -> list[UploadFile]:
    """The picked videos, refused before any is saved: none, too many, not
    a video, too big."""
    picked = [f for f in files if f.filename]
    if not picked:
        msg = "Pick at least one video"
        raise RefusedError(msg)
    most = settings.web.handmade_max_files
    if len(picked) > most:
        msg = f"At most {most} videos at once"
        raise RefusedError(msg)
    biggest = settings.web.handmade_max_upload_mb
    for f in picked:
        name = require_upload_kind(f.filename, VIDEO_KINDS)
        if (f.size or 0) > biggest * _MB:
            msg = f"{name} is over {biggest} MB"
            raise RefusedError(msg)
    return picked


def start(files: list[UploadFile], va_id: int) -> JobsQueued:
    """The VA's videos kept on the server, one job (one card) each."""
    ids = []
    for upload in _checked(files):
        job_dir = make_job_dir(upload_root())
        name = Path(save_upload(job_dir, upload)).name
        params = {"dir": str(job_dir), "files": [name]}
        run = {"job_dir": str(job_dir), "files": [name]}
        ids.append(start_job(TASK, params, run_handmade, run, actor=str(va_id)))
    return JobsQueued(job_ids=ids)


def run_handmade(
    job_dir: str,
    files: list[str],
    job_id: str = "",  # noqa: ARG001 -- start_job hands every run its job id
) -> list[ResultFile]:
    """The reel as the job's result (its preview is made after, as for any
    result). A file gone since the upload fails the job."""
    kept = []
    for name in files:
        path = Path(job_dir) / name
        if not path.is_file():
            msg = f"{name} is no longer on the server"
            raise FileNotFoundError(msg)
        kept.append(ResultFile(name=name, path=str(path)))
    return kept


def _card(job: Job, uploads: list[DriveUpload]) -> CloneCard:
    found = job_assets(job, FILES_PREFIX)
    return CloneCard.from_job(
        job,
        found.assets,
        found.failed_sources,
        task_label=LABEL,
        poll_prefix=PREFIX,
        result_kind="video",
        drive_uploads=uploads,
    )


def cards(jobs: list[Job]) -> list[CloneCard]:
    """One card per reel, with where it is in Drive (the owner's uploads)."""
    uploads: dict[str, list[DriveUpload]] = {}
    for upload in upload_store.for_jobs([j.id for j in jobs]):
        uploads.setdefault(upload.job_id, []).append(upload)
    return [_card(job, uploads.get(job.id, [])) for job in jobs]
