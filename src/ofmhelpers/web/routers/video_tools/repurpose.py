"""
/repurpose: one video in, N repurposed copies out (`ofmhelpers.repurpose`),
each scored against its source (`repurpose.checker`). Every knob is on the
page; a ready-made preset fills them; the current knobs download as a preset
JSON; a card's only button, Recreate, puts its video, preset and knobs
back. Both roles; a user only ever sees their own runs.
"""

import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.repurpose import Repurpose
from ofmhelpers.repurpose.devices import device_names, load_device
from ofmhelpers.repurpose.presets import Preset, load_preset, preset_names
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.task_helpers import (
    UPLOADS_ROOT,
    add_job_routes,
    assets_root_for,
    build_ordered_paths,
    job_assets,
    make_job_dir,
)
from ofmhelpers.web.schemas import Job, JobQueued
from ofmhelpers.web.schemas.repurpose import (
    CopyScore,
    Medium,
    MethodScore,
    RepurposeCard,
    RepurposeResult,
    knob_groups,
)
from ofmhelpers.web.stores.jobs import create_job, get_job, list_jobs_page, run_job
from ofmhelpers.web.templates_config import render

if TYPE_CHECKING:
    from ofmhelpers.repurpose.checker import CheckReport

router = APIRouter(prefix="/repurpose", tags=["repurpose"])

UPLOAD_ROOT = UPLOADS_ROOT / "repurpose"

TASK = "repurpose"

FILES_PREFIX = f"{router.prefix}/files"

MAX_COPIES = settings.web.uniqify_max_copies

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


def _score(report: "CheckReport") -> CopyScore:
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


def _run_repurpose(
    job_dir: str,
    source: str,
    preset: dict[str, Any],
    device: str,
    copies: int,
    preset_name: str = "",
    special: bool = False,
) -> list[RepurposeResult]:
    """Every copy with its own seed, then scored against the source."""
    # Deferred: imagehash/scipy load only where a job runs.
    from ofmhelpers.repurpose.checker import check

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


def _cards(job: Job) -> list[RepurposeCard]:
    """A done job as one card per copy; any other as its one card."""
    base: dict[str, Any] = {
        "task_label": "Repurpose",
        "poll_prefix": router.prefix,
        "result_kind": "video",
    }
    if job.status != "done":
        return [RepurposeCard.from_job(job, [], [], **base)]
    assets, failed = job_assets(job, FILES_PREFIX)
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


@router.get("")
def form(request: Request):
    recent, _total = list_jobs_page(
        {TASK}, 0, settings.web.gallery_limit, AuthMiddleware.actor(request)
    )
    return render(
        request,
        "repurpose.html",
        cards=[card for job in recent for card in _cards(job)],
        knob_groups=knob_groups(),
        presets=_presets(),
        devices=_devices(),
        default_device=DEFAULT_DEVICE,
        default_preset=DEFAULT_PRESET,
        default_copies=DEFAULT_COPIES,
        max_copies=MAX_COPIES,
    )


@router.get("/cards/{job_id}")
def cards(request: Request, job_id: str):
    """A finished job's cards as the gallery draws them (repurpose.js swaps
    them in for the ones generation.js built)."""
    job = get_job(job_id, AuthMiddleware.actor(request))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return render(request, "_repurpose_cards.html", cards=_cards(job))


@router.post("/preset.json")
def preset_json(preset: Preset) -> Response:
    """The page's knobs (and device) as a preset file."""
    return Response(
        content=preset.model_dump_json(exclude_unset=True, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="preset.json"'},
    )


@router.post("/run")
def run(
    request: Request,
    video: Annotated[list[UploadFile] | None, File()] = None,
    video_manifest: Annotated[str, Form()] = "[]",
    preset: Annotated[str, Form()] = "{}",
    preset_name: Annotated[str, Form()] = "",
    special: Annotated[bool, Form()] = False,
    device: Annotated[str, Form()] = DEFAULT_DEVICE,
    copies: Annotated[int, Form(ge=1, le=MAX_COPIES)] = DEFAULT_COPIES,
) -> JobQueued:
    if device not in dict(_devices()):
        raise HTTPException(status_code=400, detail="Unknown device")
    try:
        knobs = Preset.model_validate_json(preset)
    except ValidationError:
        raise HTTPException(
            status_code=400, detail="A knob has a value it cannot take"
        ) from None
    paths = build_ordered_paths(
        video_manifest,
        video or [],
        assets_root_for(request),
        user_id=AuthMiddleware.owner_id(request),
    )
    if len(paths) != 1:
        raise HTTPException(status_code=400, detail="Pick one video")
    job_dir = make_job_dir(UPLOAD_ROOT)
    params = {
        "source": paths[0],
        "preset": knobs.model_dump(mode="json", exclude_none=True, exclude=_PAGE_ONLY),
        "preset_name": preset_name,
        "special": special,
        "device": device,
        "copies": copies,
    }
    job_id = create_job(
        TASK, {"dir": str(job_dir), **params}, actor=AuthMiddleware.actor(request)
    )
    enqueue(
        run_job,
        job_id,
        _run_repurpose,
        {"job_dir": str(job_dir), **params},
        job_timeout=copies * settings.web.uniqify_encode_timeout_s,
    )
    return JobQueued(job_id=job_id)


add_job_routes(
    router,
    "Repurpose",
    lambda job: f"Making {job.params['copies']} copies…",
    back_url="/repurpose",
    back_label="repurpose another video",
)
