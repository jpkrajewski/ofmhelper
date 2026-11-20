import secrets
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel
lazy from fastapi import UploadFile

from ofmhelpers.cache import QueueName, enqueue_to
from ofmhelpers.config import settings
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.store import create_job, get_job, list_jobs_page
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.manifests import build_ordered_paths, keep_videos
from ofmhelpers.features.assets.store import make_job_dir, uploads_root
from ofmhelpers.features.clone.service import DrivePicker, drive_picker
from ofmhelpers.features.drive.uploads import SPOOFED_TASK
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.jobs.schema import JobsQueued
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.repurpose.models import (
    CopyScore,
    Medium,
    MethodScore,
    RepurposeCard,
    RepurposeResult,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media.repurpose.audio_check import BrokenAudioError, verify_audio
from ofmhelpers.media.repurpose.repurpose import EncodeStep, Repurpose
lazy from ofmhelpers.db.drive_folders.schema import DriveUpload
lazy from ofmhelpers.db.jobs.schema import Job
lazy from ofmhelpers.media.repurpose.checker import CheckReport, check

logger = get_logger(__name__)

TASK = SPOOFED_TASK
PREFIX = "/spoof"
FILES_PREFIX = f"{PREFIX}/files"
DEVICE_LABEL = "iPhone 16 Pro Max"
PRESET = "spoof"
DEFAULT_COPIES = 1
NO_VIDEO = "Pick one video"
NOT_FOUND = "Job not found"

_HASH_NAMES = {
    "pdq": "PDQ",
    "phash": "pHash",
    "dhash": "dHash",
    "whash": "wHash",
    "sscd": "SSCD",
}


class StillLooksLikeSourceError(RuntimeError): ...


def upload_root() -> Path:
    return uploads_root() / "repurpose"


def _score(report: CheckReport) -> CopyScore:
    shares = [(str(h.kind), h.sequence.share) for h in report.hashes] + [
        (d.kind, d.sequence.share) for d in report.descriptors
    ]
    methods = [
        MethodScore(
            method=_HASH_NAMES[kind], medium=Medium.VIDEO, percent=round(share * 100)
        )
        for kind, share in shares
    ]
    if report.tmk:
        methods.append(
            MethodScore(
                method="TMK+PDQF",
                medium=Medium.VIDEO,
                percent=round(report.tmk.level2 * 100),
            )
        )
    if report.audio:
        methods.append(
            MethodScore(
                method="Chromaprint",
                medium=Medium.AUDIO,
                percent=round(report.audio.matched_share * 100),
            )
        )
    return CopyScore(
        methods=methods,
        looks_like_source=report.looks_like_source,
        sounds_like_source=report.sounds_like_source,
    )


def _copy_name(seed: int) -> str:
    return f"IMG_{1000 + seed % 9000}.mov"


class _Made(BaseModel):
    score: CopyScore
    step: EncodeStep | None


def _made(source: Path, copy: Path, seed: int, timeout: int) -> _Made | None:
    run = (
        Repurpose(source, copy, seed=seed, timeout_s=timeout)
        .apply_json_preset(PRESET)
        .run()
    )
    try:
        verify_audio(source, copy)
    except BrokenAudioError:
        logger.warning("repurpose: seed %s broke the audio", seed, exc_info=True)
        return None
    return _Made(score=_score(check(source, copy, timeout_s=timeout)), step=run.step)


def _first_unique(source: Path, copy: Path, seed: int) -> RepurposeResult:
    timeout = settings.web.uniqify_encode_timeout_s
    tries = settings.repurpose.attempts
    for attempt in range(1, tries + 1):
        made = _made(source, copy, seed + attempt, timeout)
        if made is not None and made.score.unique:
            return RepurposeResult(
                name=copy.name,
                path=str(copy),
                score=made.score,
                attempts=attempt,
                step=made.step,
            )
        logger.warning(
            "repurpose: attempt %s of %s did not pass: %s",
            attempt,
            tries,
            made.score.model_dump_json() if made else "broken audio",
        )
    msg = f"Still matches the source after {tries} tries"
    raise StillLooksLikeSourceError(msg)


def run_repurpose(
    job_dir: str, source: str, seed: int | None = None, **old_params: object
) -> list[RepurposeResult]:
    if seed is None:
        seed = secrets.randbits(32)
        logger.warning(
            "repurpose: a job queued before /spoof (params %s) runs as a Spoof copy, seed %s",
            sorted(old_params),
            seed,
        )
    copy = Path(job_dir) / _copy_name(seed)
    try:
        return [_first_unique(Path(source), copy, seed)]
    except BaseException:
        copy.unlink(missing_ok=True)
        raise


def _card(job: Job, uploads: list[DriveUpload]) -> RepurposeCard:
    found = job_assets(job, FILES_PREFIX)
    entries = job.result if isinstance(job.result, list) else []
    result = RepurposeResult.model_validate(entries[0]) if entries else None
    drawn = result.step.video if result and result.step else None
    return RepurposeCard.from_job(
        job,
        found.assets,
        found.failed_sources,
        task_label="Spoof",
        poll_prefix=PREFIX,
        result_kind="video",
        drive_uploads=uploads,
        score=result.score if result else None,
        attempts=result.attempts if result else 1,
        mirrored=drawn.mirror if drawn else None,
        turn=drawn.rotate if drawn else None,
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


def cards_view(job_id: str, actor: str, user_id: int) -> CardsView:
    return CardsView(cards=cards_of(job_id, actor), **dict(drive_picker(user_id)))


class PageView(CardsView):
    prefill_video: list[str]
    device_label: str
    default_copies: int
    max_copies: int


def page_view(
    actor: str, user_id: int, assets_root: Path, videos: list[str]
) -> PageView:
    recent = list_jobs_page({TASK}, 0, settings.web.gallery_limit, actor).jobs
    return PageView(
        cards=cards(recent),
        **dict(drive_picker(user_id)),
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
            job_id,
            run_repurpose,
            {"job_dir": str(job_dir), **params},
            job_timeout=settings.repurpose.job_timeout_s,
        )
        job_ids.append(job_id)
    return JobsQueued(job_ids=job_ids)
