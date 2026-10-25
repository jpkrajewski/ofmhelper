"""
/clone-links: reel links + subject image(s) -> one Wan 3.0 run per link,
made exactly like a /clone run (`clone._run_clone`: provider, export,
mirror), each result then checked for glitches and uploaded into the
clicker's Drive (web/clone_links/).

Check (`POST /check`, instant, nothing downloaded): the pasted text read as
links (`clone_links.links`: one per line, cleaned, each once). Any line that
is not exactly one link of a site yt-dlp has an extractor for
(`generic.site_of`) refuses the paste -- "Please put one link per line",
every such line and why, the box kept as pasted; else the box gets the
cleaned list.

Generate: the paste checked again, then the page's dialog previews every link
(`POST /previews`, one request streaming a line per link, read in
parallel: `clone_links.preview` -- cover and length read by yt-dlp without
downloading, cached in Redis per link, priced like
/clone's panel: length x credits per second x $ per credit). One over the
provider's limit (Wan 3.0: 15 s) or unreadable is shown and cannot be
ticked; the VA unticks what they do not want and confirms the total.
`/run` checks the settings and the links again and queues one run per
ticked link: the worker downloads it (`_download`, as the clicker, with
their cookies.txt), refuses one over the limit before anything is paid,
then runs the clone. Google Drive must be connected, since that is where
every result goes (`clone_links.review`): GOOD REELS or TRIAL REELS, the
card showing the verdict, the glitches and the model's reasoning, and the
Drive link.
"""

import subprocess
import time
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ofmhelpers.aigenproviders.errors import InvalidInputError
from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.downloaders.cookies import cookie_file
from ofmhelpers.downloaders.generic import site_of
from ofmhelpers.downloaders.media import download
from ofmhelpers.exports import exports
from ofmhelpers.log import get_logger
from ofmhelpers.utils.video_probe import duration_s
from ofmhelpers.web.api_keys import get_api_keys
from ofmhelpers.web.clone_links.links import read_links
from ofmhelpers.web.clone_links.models import (
    CheckedLinks,
    CheckRefused,
    PreviewsRequest,
    RecreateLinks,
)
from ofmhelpers.web.clone_links.preview import (
    THUMB_KEY,
    Price,
    preview_all,
    thumb_file,
)
from ofmhelpers.web.clone_links.review import NOT_CONNECTED, start_reviews
from ofmhelpers.web.drive_space import drive_space
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.generation.clone import (
    CLONE_TARGETS,
    DEFAULT_PROMPT,
    DEFAULT_RESOLUTION,
    RESOLUTIONS,
    CloneTarget,
    _clone_values,
    _export_label,
    _require_kind,
    _run_clone,
    clip_duration,
    clone_pricing,
)
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    build_ordered_paths,
    classify_kind,
    job_assets,
    register_generated_asset,
)
from ofmhelpers.web.schemas import (
    CloneLinksCard,
    CloneQueued,
    Job,
    ProviderChoice,
    ResultFile,
)
from ofmhelpers.web.schemas.clone_ai_checks import CloneFromLink
from ofmhelpers.web.stores import clone_from_links
from ofmhelpers.web.stores import cookies as login_cookies
from ofmhelpers.web.stores.jobs import (
    count_hidden_jobs,
    create_job,
    get_job,
    list_batch,
    list_jobs_page,
    run_job,
)
from ofmhelpers.web.stores.users import refresh_token
from ofmhelpers.web.templates_config import render, render_partial, render_polled

logger = get_logger(__name__)

router = APIRouter(prefix="/clone-links", tags=["clone-links"])

FILES_PREFIX = f"{router.prefix}/files"
THUMBS_PREFIX = f"{router.prefix}/thumb"
# Covers are kept on disk under their link's hash: fine to cache a day.
_THUMB_CACHE = {"Cache-Control": "private, max-age=86400"}

TASK = "clone_links"
LABEL = "Clone links"


class ReviewState(BaseModel):
    """A card's review block, rendered, and whether it can still change on
    its own (the page stops polling a settled one)."""

    job_id: str
    settled: bool
    html: str


def _owner(job: Job | None) -> int | None:
    """The user a job belongs to (its actor is their id)."""
    actor = job.actor if job else None
    return int(actor) if actor and actor.isdigit() else None


# --- The run (worker) ----------------------------------------------------------


class LinkFetchError(RuntimeError):
    """The link gave no usable video; the message says why (its card)."""


def _download(url: str, assets_root: str, user_id: int | None) -> tuple[str, float]:
    """The link's first video, downloaded as `user_id` (their cookies.txt,
    read here: never in the queued job), kept in their part of the asset
    store, and its length."""
    with cookie_file(login_cookies.for_user(user_id)) as cookiefile:
        result = download(url, cookiefile)
    if not result.success:
        raise LinkFetchError(result.error or "Nothing was downloaded")
    video = next(
        (p for p in result.output_paths if classify_kind(p.name) == "video"), None
    )
    if video is None:
        msg = "No video at this link (a photo post?)"
        raise LinkFetchError(msg)
    kept = register_generated_asset(video, Path(assets_root))
    if kept is None:
        msg = "The video could not be kept"
        raise LinkFetchError(msg)
    try:
        seconds = duration_s(kept, settings.web.ffmpeg_timeout_s)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        msg = "The video cannot be read"
        raise LinkFetchError(msg) from exc
    logger.info("clone-links: %s -> %s (%.1f s)", url, kept.name, seconds)
    return str(kept.resolve()), seconds


def run_clone_link(
    job_id: str,
    api_key: str,
    assets_root: str,
    link: str,
    provider: str,
    prompt: str,
    resolution: str,
    reference_images: list[str],
    audio: bool = False,
    **params: Any,
) -> list[ResultFile]:
    """One run (worker, and recovery's re-run): the link's video downloaded
    as the job's owner and refused over the provider's limit before anything
    is paid, the /clone run, then a review of its video
    (`review.start_reviews`, never raises)."""
    # Runs queued while Check still downloaded carried their video.
    params.pop("reference_videos", None)
    params.pop("duration", None)
    owner = _owner(get_job(job_id))
    target = CLONE_TARGETS[provider]
    video, seconds = _download(link, assets_root, owner)
    if seconds > target.max_reference_s:
        msg = f"This video is {seconds:.1f} s: the most is {target.max_reference_s} s"
        raise InvalidInputError(msg)
    duration = clip_duration(target.spec, seconds)
    target.spec.check(
        _clone_values(
            target, prompt, resolution, duration, audio, reference_images, video
        )
    )
    results = _run_clone(
        job_id,
        api_key,
        assets_root,
        provider=provider,
        prompt=prompt,
        resolution=resolution,
        duration=duration,
        reference_images=reference_images,
        reference_videos=[video],
        audio=audio,
        link=link,
        **params,
    )
    if owner is None:
        logger.warning("clone-links %s has no owner: not reviewed", job_id)
    else:
        start_reviews(job_id, owner, [r.path for r in results])
    return results


# --- Check and preview -----------------------------------------------------------


def _read(text: str) -> list[str]:
    """The pasted links, cleaned, each once. Raises 400: a refused paste
    (`CheckRefused`: every line that is not one link of a video site),
    nothing pasted, too many."""
    pasted = read_links(text, video_site=lambda url: site_of(url) is not None)
    if pasted.bad_lines:
        raise HTTPException(
            status_code=400,
            detail=CheckRefused(
                message="Please put one link per line", lines=pasted.bad_lines
            ).model_dump(),
        )
    if not pasted.links:
        raise HTTPException(status_code=400, detail="Paste at least one link")
    cap = settings.clone_links.max_links
    if len(pasted.links) > cap:
        raise HTTPException(
            status_code=400,
            detail=f"At most {cap} links at a time ({len(pasted.links)} pasted)",
        )
    return pasted.links


@router.post("/check")
def check(links: Annotated[str, Form()] = "") -> CheckedLinks:
    """The paste cleaned (each link once), or refused naming every bad
    line. Nothing is downloaded."""
    return CheckedLinks(links=_read(links))


def _target(provider: str) -> CloneTarget:
    target = CLONE_TARGETS.get(provider or settings.aigen.default_provider)
    if target is None:
        raise HTTPException(status_code=400, detail="That provider cannot clone")
    return target


def _resolution(resolution: str) -> str:
    if resolution.upper() not in RESOLUTIONS:
        raise HTTPException(status_code=400, detail="Unknown resolution")
    return resolution.upper()


def _price(provider: str, resolution: str) -> Price:
    """A source second's price at `provider` and `resolution` (what /clone's
    panel charges), and the longest video it takes."""
    target = _target(provider)
    price = clone_pricing().get(provider)
    return Price(
        credits_per_s=price.credits_per_video_s.get(resolution, 0.0) if price else 0.0,
        usd_per_credit=price.usd_per_credit if price else 0.0,
        max_s=target.max_reference_s,
    )


@router.post("/previews")
def previews(request: Request, body: PreviewsRequest) -> StreamingResponse:
    """Generate's dialog: every link's cover, length and price, read without
    downloading (as the clicker, with their cookies.txt), all in parallel;
    one JSON line (`LinkPreview`) per link as each is done, cached ones
    first. The page fills each tile as its line arrives."""
    urls = _read("\n".join(body.urls))
    provider = body.provider or settings.aigen.default_provider
    price = _price(provider, _resolution(body.resolution or DEFAULT_RESOLUTION))
    found = preview_all(urls, AuthMiddleware.owner_id(request), price, THUMBS_PREFIX)
    return StreamingResponse(
        (p.model_dump_json() + "\n" for p in found), media_type="application/x-ndjson"
    )


@router.get("/thumb/{key}")
def thumb(key: str) -> FileResponse:
    """A previewed link's cover, kept small on disk (`clone_links.preview`):
    only a file already made, never a fetch."""
    path = thumb_file(key) if THUMB_KEY.match(key) else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="No cover")
    return FileResponse(path, media_type="image/webp", headers=_THUMB_CACHE)


# --- Generate ------------------------------------------------------------------


def _queue(request: Request, api_key: str, params: dict[str, Any]) -> str:
    """One run's job. The key goes to the worker only, never into the
    params its page shows."""
    job_id = create_job(TASK, params, actor=AuthMiddleware.actor(request))
    enqueue(
        run_job,
        job_id,
        run_clone_link,
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
    links: Annotated[list[str] | None, Form()] = None,
):
    """One run per ticked link; their job ids and cards, newest first like
    the gallery. Each link's video is downloaded by its own run."""
    provider = provider or settings.aigen.default_provider
    target = _target(provider)
    if not api_key.strip():
        raise HTTPException(status_code=400, detail="API key is required")
    export = export or settings.exports.default
    if export not in exports():
        raise HTTPException(status_code=400, detail="Unknown export")
    resolution = _resolution(resolution)
    user = AuthMiddleware.user(request)
    if not refresh_token(user.id):
        raise HTTPException(status_code=400, detail=NOT_CONNECTED)
    urls = _read("\n".join(links or []))
    image_paths = build_ordered_paths(
        image_manifest,
        image or [],
        assets_root_for(request),
        user_id=AuthMiddleware.owner_id(request),
    )
    max_images = settings.web.clone_max_images
    if not 1 <= len(image_paths) <= max_images:
        raise HTTPException(
            status_code=400, detail=f"Pick between 1 and {max_images} subject images"
        )
    _require_kind(image_paths, "image", "The subject")

    shared = {
        "provider": provider,
        "model": target.model,
        "prompt": prompt,
        "resolution": resolution,
        "audio": audio,
        "export": export,
        "mirror": mirror,
        "reference_images": image_paths,
        # One per Generate: a failed run's Recreate brings back its
        # batch's failed links (`recreate_links`).
        "batch": uuid.uuid4().hex[:8],
    }
    job_ids = [
        _queue(request, api_key.strip(), {**shared, "link": url}) for url in urls
    ]
    actor = AuthMiddleware.actor(request)
    jobs = [job for j in reversed(job_ids) if (job := get_job(j, actor)) is not None]
    html = "".join(
        render_partial("_clone_links_card.html", job=card) for card in _cards(jobs)
    )
    return CloneQueued(job_ids=job_ids, html=html)


# --- Gallery and reviews -------------------------------------------------------


def _card(job: Job, review: CloneFromLink | None, now: float) -> CloneLinksCard:
    return CloneLinksCard.from_job(
        job,
        *job_assets(job, FILES_PREFIX),
        task_label=_export_label(job.params),
        poll_prefix=router.prefix,
        result_kind="video",
        review=review,
        review_cut_off=bool(
            review and review.cut_off(now, settings.clone_links.review_stale_s)
        ),
    )


def _cards(jobs: list[Job]) -> list[CloneLinksCard]:
    # A run has one video: its review is file 0's.
    reviews = {
        r.job_id: r
        for r in clone_from_links.for_jobs([j.id for j in jobs])
        if r.file_index == 0
    }
    now = time.time()
    return [_card(j, reviews.get(j.id), now) for j in jobs]


def _gallery_page(
    request: Request, offset: int, hidden: bool = False
) -> tuple[list[CloneLinksCard], int | None]:
    """One page of the session's cards, newest first, and the next page's
    offset (None after the last). `hidden`: only the hidden cards."""
    limit = settings.web.gallery_limit
    jobs, total = list_jobs_page(
        {TASK}, offset, limit, AuthMiddleware.actor(request), hidden=hidden
    )
    next_offset = offset + limit
    return _cards(jobs), next_offset if next_offset < total else None


@router.get("")
def form(request: Request):
    gallery, next_offset = _gallery_page(request, 0)
    user = AuthMiddleware.user(request)
    return render(
        request,
        "clone_links.html",
        gallery=gallery,
        next_offset=next_offset,
        hidden_count=count_hidden_jobs({TASK}, AuthMiddleware.actor(request)),
        providers=ProviderChoice.all(list(CLONE_TARGETS)),
        selected_provider=settings.aigen.default_provider,
        api_keys=get_api_keys(request),
        default_prompt=DEFAULT_PROMPT,
        default_resolution=DEFAULT_RESOLUTION,
        resolutions=RESOLUTIONS,
        max_images=settings.web.clone_max_images,
        max_links=settings.clone_links.max_links,
        live_poll_s=settings.web.gallery_live_poll_s,
        exports=list(exports().values()),
        default_export=settings.exports.default,
        default_mirror=settings.exports.mirror,
        drive_ready=bool(refresh_token(user.id)),
        drive_space=drive_space(user.id),
    )


@router.get("/gallery")
def gallery_page(
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    hidden: Annotated[bool, Query()] = False,
):
    """The next page of cards (gallery-scroll.js); `hidden`: only the hidden
    cards (hidden-cards.js's "Show hidden")."""
    gallery, next_offset = _gallery_page(request, offset, hidden)
    return render(
        request,
        "clone_links_gallery_fragment.html",
        gallery=gallery,
        next_offset=next_offset,
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request):
    """The newest page of cards, no sentinel: what live-gallery.js polls."""
    gallery, _ = _gallery_page(request, 0)
    return render_polled(
        request, "clone_links_gallery_fragment.html", gallery=gallery, next_offset=None
    )


def _states(jobs: list[Job]) -> list[ReviewState]:
    return [
        ReviewState(
            job_id=card.job_id,
            settled=card.review_settled,
            html=render_partial("_clone_from_link.html", job=card),
        )
        for card in _cards(jobs)
    ]


def _own_runs(request: Request, job_ids: list[str]) -> list[Job]:
    actor = AuthMiddleware.actor(request)
    return [
        job
        for job_id in dict.fromkeys(job_ids)
        if (job := get_job(job_id, actor)) is not None and job.task == TASK
    ]


def _links_to_recreate(job: Job, actor: str) -> list[str]:
    """A failed run's: every failed run of its Generate, in paste order
    (ones hidden since left out, this one kept). A done run's: its own."""
    batch = job.params.get("batch")
    if job.status != "failed" or not batch:
        return [job.params["link"]]
    return [
        run.params["link"]
        for run in list_batch(TASK, batch, actor)
        if run.status == "failed" and (run.hidden_at is None or run.id == job.id)
    ]


@router.get("/recreate/{job_id}")
def recreate_links(request: Request, job_id: str) -> RecreateLinks:
    """The links a card's Recreate puts back in the box."""
    actor = AuthMiddleware.actor(request)
    job = get_job(job_id, actor)
    if job is None or job.task != TASK:
        raise HTTPException(status_code=404, detail="Not found")
    return RecreateLinks(links=_links_to_recreate(job, actor))


@router.get("/reviews")
def reviews(
    request: Request, job: Annotated[list[str], Query(max_length=200)]
) -> list[ReviewState]:
    """These runs' review blocks: what the page polls while reviews run."""
    return _states(_own_runs(request, job))


add_job_routes(
    router,
    LABEL,
    lambda _job: "Generating video… this can take a few minutes.",
    back_url="/clone-links",
    back_label="clone more",
    show_inputs=True,
)
