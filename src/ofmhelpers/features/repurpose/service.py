import secrets
from pathlib import Path
from typing import Any, Protocol

lazy from fastapi import UploadFile

from ofmhelpers.cache import QueueName, enqueue_to
from ofmhelpers.config import settings
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.store import create_job, get_job, list_jobs_page
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.manifests import build_ordered_paths, keep_videos
from ofmhelpers.features.assets.store import make_job_dir, uploads_root
from ofmhelpers.features.clone.service import DrivePicker, drive_picker
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.repurpose.models import RepurposeCard
from ofmhelpers.features.storage.constants import StorageTask
from ofmhelpers.log import get_logger
from ofmhelpers.media.repurpose.pipelines.models import RepurposeResult
from ofmhelpers.media.repurpose.pipelines.pipeline import Pipeline
lazy from ofmhelpers.db.drive_folders.schema import DriveUpload
lazy from ofmhelpers.db.jobs.schema import Job
lazy from ofmhelpers.features.storage.service import StorageServiceUploads

logger = get_logger(__name__)

TASK = StorageTask.SPOOFED
PREFIX = "/spoof"
FILES_PREFIX = f"{PREFIX}/files"
DEVICE_LABEL = "iPhone 16 Pro Max"
PIPELINE = "spoof"
DEFAULT_COPIES = 1
NO_VIDEO = "Pick one video"
NOT_FOUND = "Job not found"


def upload_root() -> Path:
    return uploads_root() / "repurpose"


def _copy_name(seed: int) -> str:
    return f"IMG_{1000 + seed % 9000}.mov"


def run_repurpose(
    job_dir: str,
    source: str,
    seed: int | None = None,
    preset: dict[str, Any] | None = None,
    device: str | None = None,
    copies: int | None = None,
    preset_name: str | None = None,
    special: bool | None = None,
) -> list[RepurposeResult]:
    if seed is None:
        seed = secrets.randbits(32)
        old_params = {
            "preset": preset,
            "device": device,
            "copies": copies,
            "preset_name": preset_name,
            "special": special,
        }
        logger.warning(
            "repurpose: a job queued before /spoof (params %s) runs as a Spoof copy, seed %s",
            sorted(name for name, value in old_params.items() if value is not None),
            seed,
        )
    copy = Path(job_dir) / _copy_name(seed)
    return Pipeline.paths(source, copy).seed(seed).load(PIPELINE).run()


def _card(job: Job, uploads: list[DriveUpload]) -> RepurposeCard:
    found = job_assets(job, FILES_PREFIX)
    entries = job.result if isinstance(job.result, list) else []
    result = RepurposeResult.model_validate(entries[0]) if entries else None
    drawn = result.step.video if result and result.step else None
    return RepurposeCard.from_job(
        job,
        found.assets,
        found.failed_sources,
        {
            "task_label": "Spoof",
            "poll_prefix": PREFIX,
            "result_kind": "video",
            "drive_uploads": uploads,
            "mirrored": drawn.mirror if drawn else None,
            "turn": drawn.rotate if drawn else None,
        },
    )


def cards(jobs: list[Job]) -> list[RepurposeCard]:
    uploads: dict[str, list[DriveUpload]] = {}
    for upload in upload_store.for_jobs([j.id for j in jobs]):
        uploads.setdefault(upload.job_id, []).append(upload)
    return [_card(j, uploads.get(j.id, [])) for j in jobs]


def cards_of(job_id: str, actor: str) -> list[RepurposeCard]:
    job = get_job(job_id, actor)
    if job is None:
        raise NotFoundError(NOT_FOUND)
    return cards([job])


class CardsView(DrivePicker):
    cards: list[RepurposeCard]


def cards_view(job_id: str, actor: str, uploads: StorageServiceUploads) -> CardsView:
    return CardsView(cards=cards_of(job_id, actor), **dict(drive_picker(uploads)))


class PageView(CardsView):
    prefill_video: list[str]
    device_label: str
    default_copies: int
    max_copies: int


def page_view(
    actor: str, uploads: StorageServiceUploads, assets_root: Path, videos: list[str]
) -> PageView:
    recent = list_jobs_page({TASK}, 0, settings.web.gallery_limit, actor).jobs
    return PageView(
        cards=cards(recent),
        **dict(drive_picker(uploads)),
        prefill_video=keep_videos(videos, assets_root)[:1],
        device_label=DEVICE_LABEL,
        default_copies=DEFAULT_COPIES,
        max_copies=settings.repurpose.max_copies,
    )


class Submitted(Protocol):
    video: list[UploadFile]
    video_manifest: str
    copies: int


def start(form: Submitted, actor: str, owner_id: int, assets_root: Path) -> JobsQueued:
    paths = build_ordered_paths(
        form.video_manifest, form.video, assets_root, user_id=owner_id
    )
    if len(paths) != 1:
        raise RefusedError(NO_VIDEO)
    job_ids = []
    for _ in range(form.copies):
        job_dir = make_job_dir(upload_root())
        seed = secrets.randbits(32)
        params = {"source": paths[0], "seed": seed}
        job_id = create_job(TASK, {"dir": str(job_dir), **params}, actor=actor)
        enqueue_to(
            QueueName.MEDIA,
            run_job,
            (job_id, run_repurpose, {"job_dir": str(job_dir), **params}),
            job_timeout=settings.repurpose.job_timeout_s,
        )
        job_ids.append(job_id)
    return JobsQueued(job_ids=job_ids)
