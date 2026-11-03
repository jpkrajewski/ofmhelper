"""What /clone does behind its routes (web/routers/generation/clone.py): a
submit's checks and runs, the cards, the Drive picker.

Each run's duration is its source video's length, clamped to the model's
schema range. Every video is probed, held to the target's `max_reference_s`,
and every run checked against the model's schema before any job is queued, so
a bad file fails the whole submit up front instead of after some runs were
already paid for.

A finished run's video can be uploaded into one or more Instagram accounts'
Drive folders, picked in the card's Drive dialog (`/clone/drive-upload`,
features/drive/uploads.py); each card says where its video went."""

import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.stores import drive_uploads as upload_store
from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.db.stores.users import refresh_token
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.clone.export import export_choice
from ofmhelpers.features.clone.pricing import clip_duration
from ofmhelpers.features.clone.targets import CloneTarget, clone_targets, clone_values
from ofmhelpers.features.drive.uploads import targets as drive_targets
from ofmhelpers.integrations.ai_providers.errors import InvalidInputError
from ofmhelpers.media.exports import exports
from ofmhelpers.schemas import CloneCard, DriveUpload, Job, UploadTarget
from ofmhelpers.telemetry import in_this_trace

TASK = "clone"
PREFIX = "/clone"
FILES_PREFIX = f"{PREFIX}/files"


class CloneRefusedError(Exception):
    """A submit nothing was queued for; the message says why (shown on the
    page)."""


class RunChoices(BaseModel):
    """A submit's picks, checked."""

    provider: str
    target: CloneTarget
    api_key: str
    export: str
    resolution: str


class CloneRun(BaseModel):
    """One run's job params: what its page and Recreate read, never a key."""

    provider: str
    model: str
    prompt: str
    resolution: str
    audio: bool
    export: str
    mirror: bool
    reference_images: list[str]
    duration: int
    reference_videos: list[str]


class DrivePicker(BaseModel):
    """What the cards' Drive dialog needs: the folders this person may
    upload into, and whether Google Drive is connected."""

    drive_targets: list[UploadTarget]
    drive_ready: bool


def _shown_name(path: str) -> str:
    return Path(path).name.split("__", 1)[-1]


def check_choices(
    provider: str, api_key: str, export: str, resolution: str
) -> RunChoices:
    """The form's picks with their defaults, before any upload is saved."""
    provider = provider or settings.aigen.default_provider
    target = clone_targets().get(provider)
    if target is None:
        msg = "That provider cannot clone"
        raise CloneRefusedError(msg)
    if not api_key.strip():
        msg = "API key is required"
        raise CloneRefusedError(msg)
    export = export or settings.exports.default
    if export not in exports():
        msg = "Unknown export"
        raise CloneRefusedError(msg)
    if resolution.upper() not in target.resolutions:
        msg = "Unknown resolution"
        raise CloneRefusedError(msg)
    return RunChoices(
        provider=provider,
        target=target,
        api_key=api_key.strip(),
        export=export,
        resolution=resolution.upper(),
    )


def video_seconds(path: str, probe: Callable[[Path, int], float]) -> float:
    try:
        return probe(Path(path), settings.web.ffmpeg_timeout_s)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        msg = f"Could not read the length of {_shown_name(path)}"
        raise CloneRefusedError(msg) from exc


def require_kind(paths: list[str], kind: str, label: str) -> None:
    if any(classify_kind(p) != kind for p in paths):
        msg = f"{label} must be {kind} files"
        raise CloneRefusedError(msg)


def _require_counts(images: list[str], videos: list[str]) -> None:
    max_images = settings.web.clone_max_images
    if not 1 <= len(images) <= max_images:
        msg = f"Pick between 1 and {max_images} subject images"
        raise CloneRefusedError(msg)
    max_videos = settings.web.clone_max_videos
    if not 1 <= len(videos) <= max_videos:
        msg = f"Pick between 1 and {max_videos} videos"
        raise CloneRefusedError(msg)


def _require_max_length(paths: list[str], seconds: list[float], limit: int) -> None:
    for path, length in zip(paths, seconds, strict=True):
        if length > limit:
            msg = (
                f"{_shown_name(path)} is {length:.1f} s; "
                f"Wan 3.0 takes at most {limit} s"
            )
            raise CloneRefusedError(msg)


def plan_runs(
    choices: RunChoices,
    prompt: str,
    audio: bool,
    mirror: bool,
    images: list[str],
    videos: list[str],
    probe: Callable[[str], float],
) -> list[CloneRun]:
    """One run per video, every run getting all the images; refused whole if
    any file or run is bad."""
    _require_counts(images, videos)
    require_kind(images, "image", "The subject")
    require_kind(videos, "video", "Videos")
    target = choices.target
    # Probed side by side (each is its own ffprobe process), in picked order;
    # the first unreadable video still fails the whole submit.
    with ThreadPoolExecutor(settings.web.clone_probe_workers) as pool:
        seconds = list(pool.map(in_this_trace(probe), videos))
    _require_max_length(videos, seconds, target.max_reference_s)
    durations = [clip_duration(target.spec, s) for s in seconds]
    try:
        for video, duration in zip(videos, durations, strict=True):
            target.spec.check(
                clone_values(
                    target, prompt, choices.resolution, duration, audio, images, video
                )
            )
    except InvalidInputError as exc:
        raise CloneRefusedError(str(exc)) from None
    return [
        CloneRun(
            provider=choices.provider,
            model=target.model,
            prompt=prompt,
            resolution=choices.resolution,
            audio=audio,
            export=choices.export,
            mirror=mirror,
            reference_images=images,
            duration=duration,
            reference_videos=[video],
        )
        for video, duration in zip(videos, durations, strict=True)
    ]


def export_label(params: dict[str, Any]) -> str:
    """What the run was exported as (Original, CapCut, a phone), by its
    label; runs from before `export` stored only the `capcut` bool."""
    name = export_choice(params.get("export"), bool(params.get("capcut")))
    choice = exports().get(name)
    return choice.label if choice else name


def card(job: Job, uploads: list[DriveUpload]) -> CloneCard:
    # Imported here, not at the top: the asset store still lives in the routers
    # package, whose __init__ imports every router -- and /clone imports this.
    from ofmhelpers.web.routers.task_helpers import job_assets

    return CloneCard.from_job(
        job,
        *job_assets(job, FILES_PREFIX),
        task_label=export_label(job.params),
        poll_prefix=PREFIX,
        result_kind="video",
        drive_uploads=uploads,
    )


def cards(jobs: list[Job]) -> list[CloneCard]:
    uploads: dict[str, list[DriveUpload]] = {}
    for upload in upload_store.for_jobs([j.id for j in jobs]):
        uploads.setdefault(upload.job_id, []).append(upload)
    return [card(j, uploads.get(j.id, [])) for j in jobs]


def drive_picker(user_id: int) -> DrivePicker:
    found = drive_targets(user_id)
    return DrivePicker(
        # Where content is needed first: fewest files waiting unposted
        # (never read: last), then the most followed.
        drive_targets=sorted(
            found,
            key=lambda t: (t.waiting is None, t.waiting or 0, -(t.followers or 0)),
        ),
        drive_ready=bool(refresh_token(user_id)),
    )


def upload_states(job_ids: list[str], actor: str) -> list[DriveUpload]:
    """Where these runs' videos are in Drive; only `actor`'s own runs."""
    return upload_store.for_jobs([j for j in job_ids if get_job(j, actor) is not None])
