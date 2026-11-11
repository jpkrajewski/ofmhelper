"""What /repurpose does behind its routes
(web/routers/video_tools/repurpose.py): one video in, N repurposed copies out
(`ofmhelpers.media.repurpose`), each scored against its source
(`repurpose.checker`); what its page shows; a run's cards."""

import secrets
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError
lazy from fastapi import UploadFile

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job, get_job, list_jobs_page
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.manifests import build_ordered_paths
from ofmhelpers.features.assets.store import make_job_dir, uploads_root
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.repurpose.models import (
    CopyScore,
    KnobGroup,
    Medium,
    MethodScore,
    RepurposeCard,
    RepurposeResult,
    knob_groups,
)
from ofmhelpers.media.repurpose import Repurpose
from ofmhelpers.media.repurpose.devices import device_names, load_device
from ofmhelpers.media.repurpose.presets import Preset, load_preset, preset_names
from ofmhelpers.schemas import Job, JobQueued
lazy from ofmhelpers.media.repurpose.checker import CheckReport, check

TASK = "repurpose"
PREFIX = "/repurpose"
FILES_PREFIX = f"{PREFIX}/files"

DEFAULT_COPIES = 3

DEFAULT_DEVICE = "strip"

DEFAULT_PRESET = "medium"

# An intermediate the iphone17 device makes itself: not a choice.
_HIDDEN_DEVICES = {"master"}

# What a preset in the page (and in a job's params) leaves out: the special
# parts are merged in when picked, the device has its own select.
_PAGE_ONLY = {"label", "video_special", "audio_special", "device", "device_special"}

# checker.HashKind values as a card names them (strings: the checker's
# imagehash/pdqhash load only in the job).
_HASH_NAMES = {
    "pdq": "PDQ",
    "phash": "pHash",
    "dhash": "dHash",
    "whash": "wHash",
    "sscd": "SSCD",
}


def upload_root() -> Path:
    return uploads_root() / "repurpose"


def _devices() -> list[tuple[str, str]]:
    return [
        (name, load_device(name).label)
        for name in device_names()
        if name not in _HIDDEN_DEVICES
    ]


def _presets() -> dict[str, dict[str, Any]]:
    """Ready-made presets as their files hold them (only what each sets:
    repurpose.js lays the special parts over the plain ones)."""
    return {
        name: load_preset(name).model_dump(mode="json", exclude_unset=True)
        for name in preset_names()
    }


def _score(report: CheckReport) -> CopyScore:
    """Every method as the share of the copy it finds in the source."""
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


def run_repurpose(
    job_dir: str,
    source: str,
    preset: dict[str, Any],
    device: str,
    copies: int,
    preset_name: str = "",
    special: bool = False,
) -> list[RepurposeResult]:
    """Every copy with its own seed, then scored against the source."""
    del preset_name, special  # the page's, kept on the job for Recreate
    directory = Path(job_dir)
    preset_file = directory / "preset.json"
    preset_file.write_text(
        Preset.model_validate({**preset, "device": device}).model_dump_json(
            exclude_none=True, indent=2
        ),
        encoding="utf-8",
    )
    ext = ".mov" if load_device(device).recorder else ".mp4"
    timeout = settings.web.uniqify_encode_timeout_s
    results = []
    for i in range(1, copies + 1):
        dst = directory / f"copy_{i:02d}{ext}"
        Repurpose(
            source, dst, seed=secrets.randbits(32), timeout_s=timeout
        ).apply_json_preset(str(preset_file)).run()
        report = check(source, dst, timeout_s=timeout)
        results.append(
            RepurposeResult(name=dst.name, path=str(dst), score=_score(report))
        )
    return results


def cards(job: Job) -> list[RepurposeCard]:
    """A done job as one card per copy; any other as its one card."""
    base: dict[str, Any] = {
        "task_label": "Repurpose",
        "poll_prefix": PREFIX,
        "result_kind": "video",
    }
    if job.status != "done":
        return [RepurposeCard.from_job(job, [], [], **base)]
    found = job_assets(job, FILES_PREFIX)
    assets, failed = found.assets, found.failed_sources
    entries = job.result if isinstance(job.result, list) else []
    scores = [RepurposeResult.model_validate(e).score for e in entries]
    if not assets:
        return [RepurposeCard.from_job(job, [], failed, **base)]
    return [
        RepurposeCard.from_job(
            job,
            [asset],
            failed,
            copy_number=i + 1,
            copies=len(assets),
            score=scores[i] if i < len(scores) else None,
            **base,
        )
        for i, asset in enumerate(assets)
    ]


def cards_of(job_id: str, actor: str) -> list[RepurposeCard]:
    """The asker's own job as the gallery draws it."""
    job = get_job(job_id, actor)
    if job is None:
        msg = "Job not found"
        raise NotFoundError(msg)
    return cards(job)


class PageView(BaseModel):
    """/repurpose: each field is a name `repurpose.html` reads."""

    cards: list[RepurposeCard]
    knob_groups: list[KnobGroup]
    presets: dict[str, dict[str, Any]]
    devices: list[tuple[str, str]]
    default_device: str
    default_preset: str
    default_copies: int
    max_copies: int


def page_view(actor: str) -> PageView:
    recent = list_jobs_page({TASK}, 0, settings.web.gallery_limit, actor).jobs
    return PageView(
        cards=[card for job in recent for card in cards(job)],
        knob_groups=knob_groups(),
        presets=_presets(),
        devices=_devices(),
        default_device=DEFAULT_DEVICE,
        default_preset=DEFAULT_PRESET,
        default_copies=DEFAULT_COPIES,
        max_copies=settings.web.uniqify_max_copies,
    )


class Submitted(Protocol):
    """The /repurpose form as its route read it (`RepurposeForm`)."""

    video: list[UploadFile]
    video_manifest: str
    preset: str
    preset_name: str
    special: bool
    device: str
    copies: int


def _knobs(form: Submitted) -> Preset:
    if form.device not in dict(_devices()):
        msg = "Unknown device"
        raise RefusedError(msg)
    try:
        return Preset.model_validate_json(form.preset)
    except ValidationError:
        msg = "A knob has a value it cannot take"
        raise RefusedError(msg) from None


def start(form: Submitted, actor: str, owner_id: int, assets_root: Path) -> JobQueued:
    knobs = _knobs(form)
    paths = build_ordered_paths(
        form.video_manifest, form.video, assets_root, user_id=owner_id
    )
    if len(paths) != 1:
        msg = "Pick one video"
        raise RefusedError(msg)
    job_dir = make_job_dir(upload_root())
    params = {
        "source": paths[0],
        "preset": knobs.model_dump(mode="json", exclude_none=True, exclude=_PAGE_ONLY),
        "preset_name": form.preset_name,
        "special": form.special,
        "device": form.device,
        "copies": form.copies,
    }
    job_id = create_job(TASK, {"dir": str(job_dir), **params}, actor=actor)
    enqueue(
        run_job,
        job_id,
        run_repurpose,
        {"job_dir": str(job_dir), **params},
        job_timeout=form.copies * settings.web.uniqify_encode_timeout_s,
    )
    return JobQueued(job_id=job_id)
