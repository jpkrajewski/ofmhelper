"""
/clone: subject images (one person, up to `clone_max_images` photos) + many
videos -> one Wan 3.0 run per video, every run getting all the images; each
run is its own card in the gallery, the same card as /generate's
(`_gallery_card.html`), newest first.

Wan 3.0 runs on whichever provider is picked: `CLONE_TARGETS` names, per
provider, its Wan 3.0 reference-to-video model and which of that model's
fields take the subject, the video and the audio switch. Each run goes
through `ai.run_ai`, like any /generate job; a provider joins /clone by one
entry there. A job's params stay provider-neutral (`reference_images`,
`reference_videos`, `resolution` ...), so the page and Recreate read every
run the same way.

Each run's duration is its source video's length, clamped to the model's
schema range. Every video is probed, held to the target's
`max_reference_s`, and every run checked against the model's schema before
any job is queued, so a bad file fails the whole submit
up front instead of after some runs were already paid for.

Each generated video is then exported as the job's `export`
(`ofmhelpers.exports`: original, CapCut, or a phone's camera recording;
default `settings.exports.default`); the exported copy becomes the result
and the provider's hosted original is dropped from it. A failed export
keeps the generated video.

A finished run's video can be uploaded into one or more Instagram accounts'
Drive folders, picked in the card's Drive dialog (`/clone/drive-upload`,
web/drive_uploads.py); each card says where its video went.
"""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfoNotFoundError

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import InvalidInputError
from ofmhelpers.aigenproviders.spec import ModelSpec
from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.exports import ExportOptions, exports, get_export
from ofmhelpers.gdrive import DriveError
from ofmhelpers.log import get_logger
from ofmhelpers.utils.video_probe import duration_s
from ofmhelpers.web.api_keys import get_api_keys
from ofmhelpers.web.drive_uploads import (
    UploadRefusedError,
    queue_uploads,
    remove_upload,
)
from ofmhelpers.web.drive_uploads import targets as drive_targets
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.generation.ai import run_ai
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    build_ordered_paths,
    classify_kind,
    job_assets,
    keep_videos,
    register_generated_asset,
)
from ofmhelpers.web.schemas import (
    CloneCard,
    CloneQueued,
    DriveUpload,
    Job,
    ProviderChoice,
    ResultFile,
    UploadRequest,
    UploadsQueued,
)
from ofmhelpers.web.stores import drive_uploads as upload_store
from ofmhelpers.web.stores.jobs import (
    count_hidden_jobs,
    create_job,
    get_job,
    list_jobs_page,
    run_job,
)
from ofmhelpers.web.stores.users import refresh_token
from ofmhelpers.web.templates_config import render, render_partial, render_polled

logger = get_logger(__name__)

router = APIRouter(prefix="/clone", tags=["clone"])

FILES_PREFIX = f"{router.prefix}/files"

TASK = "clone"

DEFAULT_PROMPT = settings.web.clone_default_prompt

# Offered in the form; each provider gets them in its own spelling.
RESOLUTIONS = ("480P", "720P", "1080P")
DEFAULT_RESOLUTION = "720P"


class CloneTarget(BaseModel):
    """One provider's Wan 3.0 reference-to-video: the catalog model, and the
    fields of its schema the subject, the video and the audio switch go in,
    and the longest reference video it takes: Wan 3.0 caps the input at
    15 s on every provider (kie.ai answers a longer one with a 422)."""

    model: str
    images: str
    video: str
    audio: str
    max_reference_s: int = 15

    @property
    def spec(self) -> ModelSpec:
        return get_spec(self.model)


class ClonePrice(BaseModel):
    """One provider's clone price, as the form's live estimate
    (clone-cost.js) reads it: credits per second of source video by
    resolution, what a credit costs, and the longest reference it takes."""

    credits_per_video_s: dict[str, float]
    usd_per_credit: float
    max_reference_s: int
    note: str


def clone_pricing() -> dict[str, ClonePrice]:
    """Every clone provider's price. A provider missing here gets no
    estimate (the panel hides)."""
    web = settings.web
    return {
        "kie": ClonePrice(
            credits_per_video_s=web.clone_credits_per_video_s,
            usd_per_credit=web.kie_usd_per_credit,
            max_reference_s=CLONE_TARGETS["kie"].max_reference_s,
            note="From your Wan 3.0 history · $50 = 10,000 credits",
        ),
        "higgsfield": ClonePrice(
            credits_per_video_s=web.clone_hf_credits_per_video_s,
            usd_per_credit=web.hf_usd_per_credit,
            max_reference_s=CLONE_TARGETS["higgsfield"].max_reference_s,
            note="Higgsfield list price at 60% off, measured on a real run · $1 = 16 credits",
        ),
    }


CLONE_TARGETS = {
    "kie": CloneTarget(
        model="kie:wan/3-0-video",
        images="reference_image_urls",
        video="reference_video_urls",
        audio="audio",
    ),
    "higgsfield": CloneTarget(
        model="higgsfield:alibaba/wan-3.0/reference-to-video",
        images="image_urls",
        video="video_urls",
        audio="generate_audio",
    ),
}


def _enum_value(spec: ModelSpec, field: str, value: str) -> str:
    """`value` in the spelling `spec` takes ("720P" vs "720p")."""
    allowed = spec.properties[field].enum or []
    return next((str(v) for v in allowed if str(v).lower() == value.lower()), value)


def clip_duration(spec: ModelSpec, seconds: float) -> int:
    """A source video's length as the nearest duration `spec` accepts."""
    field = spec.properties["duration"]
    whole = round(seconds)
    low = int(field.minimum) if field.minimum is not None else 1
    high = int(field.maximum) if field.maximum is not None else max(whole, low)
    return min(max(whole, low), high)


def _clone_values(
    target: CloneTarget,
    prompt: str,
    resolution: str,
    duration: int,
    audio: bool,
    images: list[str],
    video: str,
) -> dict[str, Any]:
    """One run's inputs, in the target model's own field names."""
    return {
        "prompt": prompt,
        "resolution": _enum_value(target.spec, "resolution", resolution),
        "aspect_ratio": "adaptive",
        "duration": duration,
        target.audio: audio,
        target.images: images,
        target.video: [video],
    }


def _run_clone(
    job_id: str,
    api_key: str,
    assets_root: str,
    provider: str,
    prompt: str,
    resolution: str,
    duration: int,
    reference_images: list[str],
    reference_videos: list[str],
    audio: bool = False,
    export: str | None = None,
    mirror: bool = False,
    capcut: bool = False,
    **page_keys: Any,
) -> list[ResultFile]:
    """One clone run. `page_keys` (`model`; older jobs also `clone_batch`,
    `clone_index`) are stored on the job for the page only. `capcut` is the choice of jobs
    from before `export` (re-run by recovery)."""
    del page_keys
    export = export or ("capcut" if capcut else "original")
    target = CLONE_TARGETS[provider]
    values = _clone_values(
        target,
        prompt,
        resolution,
        duration,
        audio,
        reference_images,
        reference_videos[0],
    )
    results = run_ai(
        job_id, api_key, assets_root, provider=provider, model=target.model, **values
    )
    options = ExportOptions(mirror=mirror)
    return [_exported(r, export, options, Path(assets_root)) for r in results]


def _exported(
    result: ResultFile, export: str, options: ExportOptions, assets_root: Path
) -> ResultFile:
    """`result` exported as `export` with `options`, next to the original and registered
    as a reusable asset like it. A remote-only result (the local download
    failed) has nothing to export and is kept as it is, and so is the
    original when the export fails: the generation was paid for and is
    already on disk, so a post-processing error must not lose it."""
    if result.path is None:
        return result
    src = Path(result.path)
    try:
        dst = get_export(export).render(src, options)
    except (
        OSError,
        RuntimeError,
        ValueError,
        ZoneInfoNotFoundError,
        subprocess.SubprocessError,
    ):
        logger.warning("%s export failed, keeping %s", export, src, exc_info=True)
        return result
    if dst == src:
        return result
    register_generated_asset(dst, assets_root)
    return ResultFile(name=dst.name, path=str(dst), remote_url=None)


def _video_seconds(path: str) -> float:
    try:
        return duration_s(Path(path), settings.web.ffmpeg_timeout_s)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        name = Path(path).name.split("__", 1)[-1]
        raise HTTPException(
            status_code=400, detail=f"Could not read the length of {name}"
        ) from exc


def _require_max_length(paths: list[str], seconds: list[float], limit: int) -> None:
    for path, length in zip(paths, seconds, strict=True):
        if length > limit:
            name = Path(path).name.split("__", 1)[-1]
            raise HTTPException(
                status_code=400,
                detail=f"{name} is {length:.1f} s; Wan 3.0 takes at most {limit} s",
            )


def _require_kind(paths: list[str], kind: str, label: str) -> None:
    wrong = [p for p in paths if classify_kind(p) != kind]
    if wrong:
        raise HTTPException(status_code=400, detail=f"{label} must be {kind} files")


def _gallery_page(
    request: Request, offset: int, hidden: bool = False
) -> tuple[list[CloneCard], int | None]:
    """One page of the session's clone cards, newest first, and the next
    page's offset (None after the last). `hidden`: only the hidden cards."""
    limit = settings.web.gallery_limit
    jobs, total = list_jobs_page(
        {TASK}, offset, limit, AuthMiddleware.actor(request), hidden=hidden
    )
    next_offset = offset + limit
    return _cards(jobs), next_offset if next_offset < total else None


def _cards(jobs: list[Job]) -> list[CloneCard]:
    uploads: dict[str, list[DriveUpload]] = {}
    for upload in upload_store.for_jobs([j.id for j in jobs]):
        uploads.setdefault(upload.job_id, []).append(upload)
    return [_card(j, uploads.get(j.id, [])) for j in jobs]


def _drive_context(request: Request) -> dict[str, Any]:
    """What the cards' Drive dialog needs: the folders this person may
    upload into, and whether Google Drive is connected."""
    user = AuthMiddleware.user(request)
    found = drive_targets(user.id)
    return {
        # Where content is needed first: fewest files waiting unposted
        # (never read: last), then the most followed.
        "drive_targets": sorted(
            found,
            key=lambda t: (t.waiting is None, t.waiting or 0, -(t.followers or 0)),
        ),
        "drive_ready": bool(refresh_token(user.id)),
    }


def _export_label(params: dict[str, Any]) -> str:
    """What the run was exported as (Original, CapCut, a phone), by its
    label; runs from before `export` stored only the `capcut` bool."""
    name = params.get("export") or ("capcut" if params.get("capcut") else "original")
    choice = exports().get(name)
    return choice.label if choice else name


def _card(job: Job, uploads: list[DriveUpload]) -> CloneCard:
    return CloneCard.from_job(
        job,
        *job_assets(job, FILES_PREFIX),
        task_label=_export_label(job.params),
        poll_prefix="/clone",
        result_kind="video",
        drive_uploads=uploads,
    )


def _prefill_videos(request: Request, paths: list[str]) -> list[str]:
    """The `?video=` paths /reels or a download hands over, kept only where
    they are a video in this session's part of the store; a stale link just
    opens with fewer videos picked."""
    return keep_videos(paths, assets_root_for(request))


@router.get("")
def form(request: Request, video: Annotated[list[str] | None, Query()] = None):
    gallery, next_offset = _gallery_page(request, 0)
    return render(
        request,
        "clone.html",
        gallery=gallery,
        next_offset=next_offset,
        hidden_count=count_hidden_jobs({TASK}, AuthMiddleware.actor(request)),
        providers=ProviderChoice.all(list(CLONE_TARGETS)),
        selected_provider=settings.aigen.default_provider,
        api_keys=get_api_keys(request),
        pricing={name: price.model_dump() for name, price in clone_pricing().items()},
        default_prompt=DEFAULT_PROMPT,
        default_resolution=DEFAULT_RESOLUTION,
        resolutions=RESOLUTIONS,
        max_videos=settings.web.clone_max_videos,
        max_images=settings.web.clone_max_images,
        live_poll_s=settings.web.gallery_live_poll_s,
        prefill_videos=_prefill_videos(request, video or []),
        max_reference_s=min(t.max_reference_s for t in CLONE_TARGETS.values()),
        exports=list(exports().values()),
        default_export=settings.exports.default,
        default_mirror=settings.exports.mirror,
        **_drive_context(request),
    )


@router.get("/gallery")
def gallery_page(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    hidden: Annotated[bool, Query()] = False,
):
    """The next page of cards, for gallery-scroll.js; `hidden`: only the hidden
    cards (hidden-cards.js's "Show hidden")."""
    gallery, next_offset = _gallery_page(request, offset, hidden)
    return render(
        request,
        "clone_gallery_fragment.html",
        gallery=gallery,
        next_offset=next_offset,
        **_drive_context(request),
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request):
    """The newest page of cards, no sentinel: what live-gallery.js polls to
    show runs started in other sessions."""
    gallery, _ = _gallery_page(request, 0)
    return render_polled(
        request,
        "clone_gallery_fragment.html",
        gallery=gallery,
        next_offset=None,
        **_drive_context(request),
    )


@router.post("/drive-upload")
def drive_upload(request: Request, body: UploadRequest) -> UploadsQueued:
    """These runs' videos into each picked account's Drive folder, on the
    worker, as the clicker's Google account."""
    try:
        uploads = queue_uploads(
            AuthMiddleware.user(request), body.job_ids, body.login_ids
        )
    except UploadRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UploadsQueued(
        queued=sum(1 for u in uploads if u.status == "queued"), uploads=uploads
    )


@router.post("/drive-uploads/{upload_id}/remove")
def drive_upload_remove(request: Request, upload_id: int) -> DriveUpload:
    """Takes an uploaded video back out of its account folder (Drive trash)."""
    try:
        return remove_upload(AuthMiddleware.user(request), upload_id)
    except UploadRefusedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DriveError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/drive-uploads")
def drive_upload_states(
    request: Request, job: Annotated[list[str], Query(max_length=200)]
) -> list[DriveUpload]:
    """Where these runs' videos are in Drive: what clone.js polls while
    uploads run. Only runs this session may see."""
    actor = AuthMiddleware.actor(request)
    mine = [j for j in job if get_job(j, actor) is not None]
    return upload_store.for_jobs(mine)


def _queue(request: Request, api_key: str, params: dict[str, Any]) -> str:
    """One run's job. The key goes to the worker only, never into the
    params its page shows."""
    job_id = create_job(TASK, params, actor=AuthMiddleware.actor(request))
    enqueue(
        run_job,
        job_id,
        _run_clone,
        {
            "job_id": job_id,
            "api_key": api_key,
            "assets_root": str(assets_root_for(request)),
            **params,
        },
    )
    return job_id


@router.post("/run")
def run(
    request: Request,
    api_key: Annotated[str, Form()] = "",
    provider: Annotated[str, Form()] = "",
    prompt: Annotated[str, Form(min_length=1)] = DEFAULT_PROMPT,
    resolution: Annotated[str, Form()] = DEFAULT_RESOLUTION,
    audio: Annotated[bool, Form()] = False,
    export: Annotated[str, Form()] = "",
    mirror: Annotated[bool, Form()] = False,
    image: Annotated[list[UploadFile] | None, File()] = None,
    image_manifest: Annotated[str, Form()] = "[]",
    videos: Annotated[list[UploadFile] | None, File()] = None,
    videos_manifest: Annotated[str, Form()] = "[]",
):
    """Queues one run per video and returns their job ids plus their cards
    rendered, newest first like the gallery, so the page shows them without
    a reload."""
    provider = provider or settings.aigen.default_provider
    target = CLONE_TARGETS.get(provider)
    if target is None:
        raise HTTPException(status_code=400, detail="That provider cannot clone")
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")
    export = export or settings.exports.default
    if export not in exports():
        raise HTTPException(status_code=400, detail="Unknown export")
    if resolution.upper() not in RESOLUTIONS:
        raise HTTPException(status_code=400, detail="Unknown resolution")
    assets_root = assets_root_for(request)
    user_id = AuthMiddleware.owner_id(request)
    image_paths = build_ordered_paths(
        image_manifest, image or [], assets_root, user_id=user_id
    )
    video_paths = build_ordered_paths(
        videos_manifest, videos or [], assets_root, user_id=user_id
    )

    max_images = settings.web.clone_max_images
    if not 1 <= len(image_paths) <= max_images:
        raise HTTPException(
            status_code=400, detail=f"Pick between 1 and {max_images} subject images"
        )
    max_videos = settings.web.clone_max_videos
    if not 1 <= len(video_paths) <= max_videos:
        raise HTTPException(
            status_code=400, detail=f"Pick between 1 and {max_videos} videos"
        )
    _require_kind(image_paths, "image", "The subject")
    _require_kind(video_paths, "video", "Videos")
    # Probed side by side (each is its own ffprobe process), in picked order;
    # the first unreadable video still fails the whole submit.
    with ThreadPoolExecutor(settings.web.clone_probe_workers) as pool:
        seconds = list(pool.map(_video_seconds, video_paths))
    _require_max_length(video_paths, seconds, target.max_reference_s)
    durations = [clip_duration(target.spec, s) for s in seconds]
    try:
        for path, duration in zip(video_paths, durations, strict=True):
            target.spec.check(
                _clone_values(
                    target, prompt, resolution, duration, audio, image_paths, path
                )
            )
    except InvalidInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    shared = {
        "provider": provider,
        "model": target.model,
        "prompt": prompt,
        "resolution": resolution.upper(),
        "audio": audio,
        "export": export,
        "mirror": mirror,
        "reference_images": image_paths,
    }
    job_ids = [
        _queue(
            request,
            api_key.strip(),
            {
                **shared,
                "duration": duration,
                "reference_videos": [path],
            },
        )
        for path, duration in zip(video_paths, durations, strict=True)
    ]

    actor = AuthMiddleware.actor(request)
    jobs = [job for j in reversed(job_ids) if (job := get_job(j, actor)) is not None]
    html = "".join(
        render_partial("_clone_card.html", job=card, **_drive_context(request))
        for card in _cards(jobs)
    )
    return CloneQueued(job_ids=job_ids, html=html)


add_job_routes(
    router,
    "Clone",
    lambda _job: "Generating video… this can take a few minutes.",
    back_url="/clone",
    back_label="clone more",
    show_inputs=True,
)
