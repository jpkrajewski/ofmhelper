"""What /clone-long does behind its routes
(web/routers/generation/clone_long.py): what its form shows, a run's card,
and a submit measured, priced, checked piece by piece and only then queued --
the stored price is the server's, never the browser's."""

import subprocess
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel
lazy from fastapi import UploadFile

from ofmhelpers.config import settings
from ofmhelpers.features.account.keys import Keys
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.manifests import build_ordered_paths, keep_videos
from ofmhelpers.features.clone_long.models import Shape
from ofmhelpers.features.clone_long.service import (
    RESOLUTIONS,
    TARGETS,
    LongClone,
    rate,
    run_long_clone,
)
from ofmhelpers.features.clone_long.video import VideoTools
from ofmhelpers.features.generate.choices import provider_choices
from ofmhelpers.features.generate.providers import CLONE_TASKS, start_provider
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.schemas import Job, ProviderChoice, TaskCard

TASK = "clone_long"
LABEL = "Clone long"
PREFIX = "/clone-long"
FILES_PREFIX = f"{PREFIX}/files"
DEFAULT_RESOLUTION = "720P"


class FormView(BaseModel):
    """The form's part of the page; each field is a name the template reads."""

    prefill_video: list[str]
    providers: list[ProviderChoice]
    selected_provider: str
    rates: dict[str, dict[str, Any]]
    max_piece_s: int
    max_video_s: int
    resolutions: list[str]
    default_resolution: str
    default_prompt: str


def form_view(actor: str, assets_root: Path, videos: list[str]) -> FormView:
    """`videos`: the `?video=` paths another page hands over. One video: a
    download sends its first (clone-long clones one video)."""
    return FormView(
        prefill_video=keep_videos(videos, assets_root)[:1],
        providers=provider_choices(list(TARGETS)),
        selected_provider=start_provider(actor, CLONE_TASKS, list(TARGETS)),
        rates={name: rate(name).model_dump() for name in TARGETS},
        max_piece_s=min(t.max_reference_s for t in TARGETS.values()),
        max_video_s=settings.clone_long.max_video_s,
        resolutions=RESOLUTIONS,
        default_resolution=DEFAULT_RESOLUTION,
        default_prompt=settings.web.clone_default_prompt,
    )


def card(job: Job) -> TaskCard:
    """A run as the shared gallery card, labelled with its price."""
    usd = job.params.get("usd")
    pieces = job.params.get("pieces")
    noun = "piece" if pieces == 1 else "pieces"
    label = f"Wan 3.0 · {pieces} {noun} · ${usd:.2f}" if usd is not None else LABEL
    found = job_assets(job, FILES_PREFIX)
    return TaskCard.from_job(
        job,
        found.assets,
        found.failed_sources,
        task_label=label,
        poll_prefix=PREFIX,
        result_kind="video",
    )


def cards(jobs: list[Job]) -> list[TaskCard]:
    return [card(job) for job in jobs]


class Submitted(Protocol):
    """The /clone-long form as its route read it (`LongCloneForm`)."""

    provider: str
    api_key: str
    prompt: str
    resolution: str
    image: list[UploadFile]
    image_manifest: str
    video: list[UploadFile]
    video_manifest: str


class Measured(BaseModel):
    """A video's length and shape as it plays."""

    seconds: float
    shape: Shape


def _one(paths: list[str], kind: str, label: str) -> str:
    if len(paths) != 1 or classify_kind(paths[0]) != kind:
        msg = f"Pick one {label}"
        raise RefusedError(msg)
    return paths[0]


def _measure(path: str) -> Measured:
    """Refused when unreadable or over the longest video taken."""
    tools = VideoTools(settings.web.ffmpeg_timeout_s)
    try:
        found = Measured(
            seconds=tools.length(Path(path)), shape=tools.shape(Path(path))
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as exc:
        msg = "Could not read the video's length"
        raise RefusedError(msg) from exc
    longest = settings.clone_long.max_video_s
    if found.seconds > longest:
        msg = f"The video is {found.seconds:.0f} s; the longest is {longest} s"
        raise RefusedError(msg)
    return found


def _require_choices(form: Submitted, owner_id: int) -> str:
    """The form's picks checked; the key the run goes out on (`owner_id`'s
    for the target's model, `Keys.for_run`)."""
    if form.provider not in TARGETS:
        msg = "That provider cannot clone"
        raise RefusedError(msg)
    api_key = Keys.of(owner_id).for_run(
        form.provider, TARGETS[form.provider].model, form.api_key
    )
    if not api_key:
        msg = "API key is required"
        raise RefusedError(msg)
    if form.resolution not in RESOLUTIONS:
        msg = "Unknown resolution"
        raise RefusedError(msg)
    return api_key


def start(form: Submitted, actor: str, owner_id: int, assets_root: Path) -> str:
    """One run queued: its job id. The key goes to the worker only, never into the params
    its page shows."""
    api_key = _require_choices(form, owner_id)
    image = _one(
        build_ordered_paths(
            form.image_manifest, form.image, assets_root, user_id=owner_id
        ),
        "image",
        "subject image",
    )
    video = _one(
        build_ordered_paths(
            form.video_manifest, form.video, assets_root, user_id=owner_id
        ),
        "video",
        "video",
    )
    measured = _measure(video)
    clone = LongClone.prepare(
        provider=form.provider,
        prompt=form.prompt or settings.web.clone_default_prompt,
        resolution=form.resolution,
        image=image,
        video=video,
        seconds=measured.seconds,
        shape=measured.shape,
    )
    params = clone.params.model_dump()
    return start_job(
        TASK,
        params,
        run_long_clone,
        {"api_key": api_key, "assets_root": str(assets_root), **params},
        actor=actor,
        job_timeout=clone.time_limit(),
    )
