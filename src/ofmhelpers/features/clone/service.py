# Copyright (C) 2026 jpkrajewski

import re
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict
lazy from fastapi import UploadFile

from ofmhelpers.config import settings
from ofmhelpers.db.drive_folders.models import UploadTarget
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.store import ParamHistory, Readers, get_job
from ofmhelpers.features.account.keys import Keys
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.manifests import build_ordered_paths, keep_files
from ofmhelpers.features.assets.store import strip_asset_hash_prefix
from ofmhelpers.features.clone.export import export_choice
from ofmhelpers.features.clone_pricing.pricing import clip_duration
from ofmhelpers.features.clone_pricing.targets import (
    CloneTarget,
    clone_targets,
    clone_values,
)
from ofmhelpers.features.jobs.models import CloneCard
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.jobs.schema import CloneSource
from ofmhelpers.features.kinds import classify_kind
from ofmhelpers.features.refusals import CloneRefusedError
from ofmhelpers.integrations.ai_providers.base import provider_class
from ofmhelpers.integrations.ai_providers.errors import InvalidInputError
from ofmhelpers.media.exports import exports
from ofmhelpers.monitoring.telemetry import in_this_trace
lazy from ofmhelpers.db.drive_folders.schema import DriveUpload
lazy from ofmhelpers.db.jobs.schema import Job
lazy from ofmhelpers.features.storage.service import StorageServiceUploads

TASK = "clone"
PREFIX = "/clone"
FILES_PREFIX = f"{PREFIX}/files"
SOURCE_THUMB_PX = 400
_SHA256 = re.compile(r"[0-9a-f]{64}")
SUBJECT_TASKS = {TASK, "clone_links"}


class RunChoices(BaseModel):
    provider: str
    target: CloneTarget
    api_key: str
    export: str
    resolution: str


class CloneRun(BaseModel):
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
    drive_targets: list[UploadTarget]
    drive_ready: bool


def _shown_name(path: str) -> str:
    return Path(path).name.split("__", 1)[-1]


def check_choices(
    provider: str,
    api_key: str,
    export: str,
    resolution: str,
    owner: int,
) -> RunChoices:
    provider = provider or settings.aigen.default_provider
    target = clone_targets().get(provider)
    if target is None:
        msg = "That provider cannot clone"
        raise CloneRefusedError(msg)
    api_key = Keys.of(owner).for_run(provider, target.model, api_key)
    if not api_key and provider_class(provider).needs_key:
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
        api_key=api_key,
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


def plan_runs(  # noqa: PLR0913, PLR0917
    choices: RunChoices,
    prompt: str,
    audio: bool,  # noqa: FBT001
    mirror: bool,  # noqa: FBT001
    images: list[str],
    videos: list[str],
    probe: Callable[[str], float],
) -> list[CloneRun]:
    _require_counts(images, videos)
    require_kind(images, "image", "The subject")
    require_kind(videos, "video", "Videos")
    target = choices.target
    with ThreadPoolExecutor(settings.web.clone_probe_workers) as pool:
        seconds = list(pool.map(in_this_trace(probe), videos))
    _require_max_length(videos, seconds, target.max_reference_s)
    durations = [clip_duration(target.spec, s) for s in seconds]
    try:
        for video, duration in zip(videos, durations, strict=True):
            target.spec.check(
                clone_values(
                    target,
                    prompt,
                    choices.resolution,
                    duration,
                    audio,
                    images,
                    video,
                ).model_dump(),
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


class Submitted(Protocol):
    provider: str
    api_key: str
    export: str
    resolution: str
    prompt: str
    audio: bool
    mirror: bool
    image: list[UploadFile]
    image_manifest: str
    videos: list[UploadFile]
    videos_manifest: str


class Submit(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor: str
    owner: int
    assets_root: Path
    run: Callable[..., Any]
    probe: Callable[[str], float]

    def _picked(self, manifest: str, files: list[UploadFile]) -> list[str]:
        return build_ordered_paths(
            manifest,
            files,
            self.assets_root,
            user_id=self.owner,
        )

    def _queue(self, run: CloneRun, api_key: str) -> str:
        params = run.model_dump()
        return start_job(
            TASK,
            params,
            self.run,
            {"api_key": api_key, "assets_root": str(self.assets_root), **params},
            actor=self.actor,
        )

    def start(self, form: Submitted) -> list[str]:
        choices = check_choices(
            form.provider,
            form.api_key,
            form.export,
            form.resolution,
            self.owner,
        )
        runs = plan_runs(
            choices,
            form.prompt,
            form.audio,
            form.mirror,
            self._picked(form.image_manifest, form.image),
            self._picked(form.videos_manifest, form.videos),
            self.probe,
        )
        return [self._queue(run, choices.api_key) for run in runs]


def export_label(params: dict[str, Any]) -> str:
    name = export_choice(params.get("export"), bool(params.get("capcut")))
    choice = exports().get(name)
    return choice.label if choice else name


def source(params: dict[str, Any]) -> CloneSource | None:
    videos = params.get("reference_videos") or []
    if not videos:
        return None
    path = Path(str(videos[0]))
    name = strip_asset_hash_prefix(path.name)
    if name == path.name or not path.is_file():
        return None
    thumb = f"/refs/thumb?path={quote(str(path), safe='')}&size={SOURCE_THUMB_PX}"
    return CloneSource(name=name, thumb_url=thumb)


def card(job: Job, uploads: list[DriveUpload]) -> CloneCard:
    found = job_assets(job, FILES_PREFIX)
    return CloneCard.from_job(
        job,
        found.assets,
        found.failed_sources,
        {
            "task_label": export_label(job.params),
            "poll_prefix": PREFIX,
            "result_kind": "video",
            "drive_uploads": uploads,
            "source": source(job.params),
        },
    )


def cards(jobs: list[Job]) -> list[CloneCard]:
    uploads: dict[str, list[DriveUpload]] = {}
    for upload in upload_store.for_jobs([j.id for j in jobs]):
        uploads.setdefault(upload.job_id, []).append(upload)
    return [card(j, uploads.get(j.id, [])) for j in jobs]


def cards_of(job_ids: list[str], actor: str) -> list[CloneCard]:
    return cards(
        [job for j in reversed(job_ids) if (job := get_job(j, actor)) is not None],
    )


def drive_picker(uploads: StorageServiceUploads) -> DrivePicker:
    found = uploads.targets()
    return DrivePicker(
        drive_targets=sorted(
            found,
            key=lambda t: (t.waiting is None, t.waiting or 0, -(t.followers or 0)),
        ),
        drive_ready=uploads.connected(),
    )


class SubjectPicks(BaseModel):
    last_used: list[str]
    most_used: list[str]


def subject_picks(actor: str | frozenset[str], assets_root: Path) -> SubjectPicks:
    web = settings.web
    subjects = ParamHistory(
        tasks=frozenset(SUBJECT_TASKS),
        actor=actor,
        field="reference_images",
    )
    return SubjectPicks(
        last_used=_stored(
            subjects.last_used_firsts,
            assets_root,
            web.clone_last_subjects,
        ),
        most_used=_stored(
            subjects.most_used_firsts,
            assets_root,
            web.clone_top_subjects,
        ),
    )


def _digest(path: str) -> str:
    return Path(path).name.split("__", 1)[0]


def _stored(
    query: Callable[[int], list[str]],
    assets_root: Path,
    limit: int,
) -> list[str]:
    found: list[str] = []
    for path in query(limit * 3):
        if len(found) == limit:
            break
        if copy := _stored_copy(path, assets_root):
            found.append(copy)
    return found


def _stored_copy(path: str, assets_root: Path) -> str | None:
    if kept := keep_files(
        [path, str(assets_root / Path(path).name)],
        assets_root,
        "image",
    ):
        return kept[0]
    digest = _digest(path)
    if not _SHA256.fullmatch(digest):
        return None
    others = sorted(str(p) for p in assets_root.glob(f"{digest}__*"))
    return next(iter(keep_files(others, assets_root, "image")), None)


def upload_states(
    job_ids: list[str],
    actors: Readers,
    team: frozenset[int],
) -> list[DriveUpload]:
    return upload_store.for_jobs(
        [j for j in job_ids if get_job(j, actors) is not None],
        team,
    )
