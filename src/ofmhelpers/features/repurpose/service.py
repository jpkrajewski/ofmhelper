# Copyright (C) 2026 jpkrajewski

import logging
import secrets
from pathlib import Path
from typing import Protocol

from ofmhelpers.cache import QueueName, enqueue_to
from ofmhelpers.config import settings
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.store import (
    create_job,
    get_job,
    list_creator_jobs_page,
    list_jobs_page,
    set_job_creator,
)
from ofmhelpers.db.tracked_accounts.store import creators_of
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.manifests import build_ordered_paths, keep_videos
from ofmhelpers.features.assets.store import make_job_dir, uploads_root
from ofmhelpers.features.clone.service import DrivePicker, drive_picker
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.repurpose.constants import MirrorChoice, SpoofRefusal
from ofmhelpers.features.repurpose.executions import JobExecutions
from ofmhelpers.features.repurpose.models import RepurposeCard
from ofmhelpers.features.storage.constants import StorageTask
from ofmhelpers.media.repurpose.pipelines.models import RepurposeResult
from ofmhelpers.media.repurpose.pipelines.pipeline import Pipeline
from ofmhelpers.media.repurpose.transformations.video import VideoSettings
lazy from ofmhelpers.db.drive_folders.schema import DriveUpload
lazy from ofmhelpers.db.jobs.schema import Job
lazy from ofmhelpers.db.tracked_accounts.schema import Creator
lazy from ofmhelpers.db.users.schema import SessionUser
lazy from ofmhelpers.features.assets.models import VideoUpload
lazy from ofmhelpers.features.storage.service import StorageServiceUploads

logger = logging.getLogger(__name__)

TASK = StorageTask.SPOOFED
PREFIX = "/spoof"
FILES_PREFIX = f"{PREFIX}/files"
DEVICE_LABEL = "iPhone 16 Pro Max"
PIPELINE = "spoof"
DEFAULT_COPIES = 4
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
    mirror: str = MirrorChoice.RANDOM,
    job_id: str | None = None,
) -> list[RepurposeResult]:
    if seed is None:
        seed = secrets.randbits(32)
        logger.warning(
            "repurpose: job %s was stored without a seed, it runs with seed %s",
            job_id,
            seed,
        )
    copy = Path(job_dir) / _copy_name(seed)
    pipeline = Pipeline.paths(source, copy).seed(seed).load(PIPELINE)
    mirror_chance = MirrorChoice(mirror).mirror_chance
    if mirror_chance is not None:
        pipeline.video(VideoSettings(mirror=mirror_chance))
    if job_id is not None:
        pipeline.keep_executions_with(JobExecutions(job_id))
    return pipeline.run()


def _creator(creators: list[Creator], creator_id: int | None) -> Creator | None:
    for creator in creators:
        if creator.id == creator_id:
            return creator
    return None


def _card(
    job: Job,
    uploads: list[DriveUpload],
    creators: list[Creator],
) -> RepurposeCard:
    creator = _creator(creators, job.creator_id)
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
            "preset": result.stage.preset if result and result.stage else None,
            "strength": result.strength if result else None,
            "picture_tries": result.picture_tries if result else None,
            "audio_tries": result.audio_tries if result else None,
            "mirror_choice": job.params.get("mirror", MirrorChoice.RANDOM),
            "kept_first_sound": result.kept_first_sound if result else False,
            "creator_id": creator.id if creator else None,
            "creator_name": creator.name if creator else None,
        },
    )


def cards(jobs: list[Job], creators: list[Creator]) -> list[RepurposeCard]:
    uploads: dict[str, list[DriveUpload]] = {}
    for upload in upload_store.for_jobs([j.id for j in jobs]):
        uploads.setdefault(upload.job_id, []).append(upload)
    return [_card(j, uploads.get(j.id, []), creators) for j in jobs]


def cards_of(job_id: str, user: SessionUser) -> list[RepurposeCard]:
    job = get_job(job_id, user.actor)
    if job is None:
        raise NotFoundError(NOT_FOUND)
    return cards([job], creators_of(user.id))


class CardsView(DrivePicker):
    cards: list[RepurposeCard]


def cards_view(
    job_id: str,
    user: SessionUser,
    uploads: StorageServiceUploads,
) -> CardsView:
    return CardsView(cards=cards_of(job_id, user), **dict(drive_picker(uploads)))


class PageView(CardsView):
    prefill_video: list[str]
    device_label: str
    default_copies: int
    max_copies: int
    creators: list[Creator]
    picked_creator: Creator | None


class Shown(Protocol):
    video: list[str]
    creator: int | None


def page_view(
    shown: Shown,
    user: SessionUser,
    uploads: StorageServiceUploads,
    assets_root: Path,
) -> PageView:
    creators = creators_of(user.id)
    picked = _creator(creators, shown.creator)
    limit = settings.web.gallery_limit
    if picked is None:
        recent = list_jobs_page({TASK}, 0, limit, user.actor).jobs
    else:
        recent = list_creator_jobs_page(TASK, picked.id, 0, limit, user.actor).jobs
    return PageView(
        cards=cards(recent, creators),
        **dict(drive_picker(uploads)),
        prefill_video=keep_videos(shown.video, assets_root)[:1],
        device_label=DEVICE_LABEL,
        default_copies=DEFAULT_COPIES,
        max_copies=settings.repurpose.max_copies,
        creators=creators,
        picked_creator=picked,
    )


class Submitted(Protocol):
    video: list[VideoUpload]
    video_manifest: str
    copies: int
    mirror: MirrorChoice
    creator: int | None


def start(form: Submitted, actor: str, owner_id: int, assets_root: Path) -> JobsQueued:
    if form.creator is not None and (
        _creator(creators_of(owner_id), form.creator) is None
    ):
        raise RefusedError(SpoofRefusal.NOT_YOUR_MODEL)
    paths = build_ordered_paths(
        form.video_manifest,
        form.video,
        assets_root,
        user_id=owner_id,
    )
    if len(paths) != 1:
        raise RefusedError(NO_VIDEO)
    job_ids = []
    for _ in range(form.copies):
        job_dir = make_job_dir(upload_root())
        seed = secrets.randbits(32)
        params = {"source": paths[0], "seed": seed, "mirror": form.mirror.value}
        job_id = create_job(TASK, {"dir": str(job_dir), **params}, actor=actor)
        if form.creator is not None:
            set_job_creator(job_id, form.creator)
        enqueue_to(
            QueueName.MEDIA,
            run_job,
            (
                job_id,
                run_repurpose,
                {"job_dir": str(job_dir), "job_id": job_id, **params},
            ),
            job_timeout=settings.repurpose.job_timeout_s,
        )
        job_ids.append(job_id)
    return JobsQueued(job_ids=job_ids)
