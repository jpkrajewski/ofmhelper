# Copyright (C) 2026 jpkrajewski

from pathlib import Path
lazy from collections.abc import Sequence

from ofmhelpers.config import settings
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.schema import Job, ResultFile
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.store import (
    make_job_dir,
    save_upload,
    uploads_root,
)
from ofmhelpers.features.jobs.models import CloneCard
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.features.refusals import RefusedError
lazy from ofmhelpers.db.drive_folders.schema import DriveUpload
lazy from ofmhelpers.features.assets.models import VideoUpload

TASK = "handmade_upload"
LABEL = "Handmade"
PREFIX = "/handmade"
FILES_PREFIX = f"{PREFIX}/files"
_MB = 1024 * 1024


def upload_root() -> Path:
    return uploads_root() / "handmade"


def _checked(files: Sequence[VideoUpload]) -> Sequence[VideoUpload]:
    picked = files
    if not picked:
        msg = "Pick at least one video"
        raise RefusedError(msg)
    most = settings.web.handmade_max_files
    if len(picked) > most:
        msg = f"At most {most} videos at once"
        raise RefusedError(msg)
    biggest = settings.web.handmade_max_upload_mb
    for f in picked:
        name = f.filename
        if (f.size or 0) > biggest * _MB:
            msg = f"{name} is over {biggest} MB"
            raise RefusedError(msg)
    return picked


def start(files: Sequence[VideoUpload], va_id: int) -> JobsQueued:
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
    job_id: str = "",  # noqa: ARG001
) -> list[ResultFile]:
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
        {
            "task_label": LABEL,
            "poll_prefix": PREFIX,
            "result_kind": "video",
            "drive_uploads": uploads,
        },
    )


def cards(jobs: list[Job]) -> list[CloneCard]:
    uploads: dict[str, list[DriveUpload]] = {}
    for upload in upload_store.for_jobs([j.id for j in jobs]):
        uploads.setdefault(upload.job_id, []).append(upload)
    return [_card(job, uploads.get(job.id, [])) for job in jobs]
