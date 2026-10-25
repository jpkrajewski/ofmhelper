"""
/clone-links: reel links + subject image(s) -> one Wan 3.0 run per link,
made exactly like a /clone run (`clone._run_clone`: provider, export,
mirror), each result then checked for glitches and uploaded into the
clicker's Drive (web/clone_links/).

Check (`POST /check`, instant, nothing downloaded): the pasted text read as
links (`clone_links.links.read_pasted`: one post per line, cleaned, each
once). Any line that is not exactly one post of a video site refuses the
paste -- "Please put one link per line", every such line and why, the box
kept as pasted; else the box gets the cleaned list.

Generate: the paste checked again, then the page's dialog previews every link
(`POST /previews`, one request streaming a line per link, read in
parallel: `clone_links.preview` -- cover and length read by yt-dlp without
downloading, cached in Redis per link, priced like
/clone's panel: length x credits per second x $ per credit). One over the
provider's limit (Wan 3.0: 15 s) or unreadable is shown and cannot be
ticked; the VA unticks what they do not want and confirms the total.
`/run` checks the settings and the links again and queues one run per
ticked link on the clone-links queue (its own worker pool: a 120-link
Generate never holds the shared queue): the worker downloads it, refuses
one over the limit before anything is paid, then runs the clone
(`clone_links.run`). Google Drive must be connected (checked before any
preview is read, and again at `/run`), since that is where every result
goes (`clone_links.review`): GOOD REELS or TRIAL REELS.

A Generate is one `batch` (the page's id for it, sent with `/run`): a
resend of the same batch -- the answer lost, a retry after an error --
returns the runs already queued and pays for nothing again. Stop
(`POST /stop/{job}`) stops a card's whole batch: every run not yet paid
fails "Stopped" when it reaches the worker; a paid one finishes.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from redis.exceptions import RedisError

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.cache import CLONE_LINKS_QUEUE, enqueue_to, get_redis
from ofmhelpers.config import settings
from ofmhelpers.exports import exports
from ofmhelpers.log import get_logger
from ofmhelpers.web.api_keys import get_api_keys
from ofmhelpers.web.clone_links import run as clone_link_run
from ofmhelpers.web.clone_links.links import read_pasted
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
from ofmhelpers.web.clone_links.review import NOT_CONNECTED
from ofmhelpers.web.clone_links.stop import stop_batch
from ofmhelpers.web.drive_space import drive_space
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.routers.generation.clone import (
    CLONE_TARGETS,
    DEFAULT_PROMPT,
    DEFAULT_RESOLUTION,
    RESOLUTIONS,
    CloneTarget,
    _export_label,
    _require_kind,
    clone_pricing,
)
from ofmhelpers.web.routers.task_helpers import (
    add_job_routes,
    assets_root_for,
    build_ordered_paths,
    job_assets,
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
from ofmhelpers.web.stores.jobs import (
    count_hidden_jobs,
    create_job,
    fail_job,
    get_job,
    list_batch,
    list_jobs_page,
    redone_links,
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
# The preview stream line by line through the host's nginx, never buffered.
_UNBUFFERED = {"X-Accel-Buffering": "no"}

TASK = "clone_links"
LABEL = "Clone links"
# A Generate's id, made by the page (`/run`'s `batch`).
BATCH = r"^[0-9a-f]{8,32}$"
# How long a sent batch is remembered as sent (a resend within it, even one
# racing the first, queues nothing; after it, its runs still answer).
_SENT_TTL_S = 24 * 3600
# A run's row made but never queued (Redis/RQ refused): its card says so.
NOT_QUEUED = "Not queued: run it again"


class ReviewState(BaseModel):
    """A card's review block, rendered, and whether it can still change on
    its own (the page stops polling a settled one)."""

    job_id: str
    settled: bool
    html: str


class BatchStopped(BaseModel):
    """`POST /stop/{job_id}`: the Generate stopped, and how many of its runs
    are still running (a paid one finishes; the rest fail "Stopped" as the
    worker reaches them)."""

    batch: str
    running: int


# --- The run (worker): web/clone_links/run.py --------------------------------


def run_clone_link(**params: Any) -> list[ResultFile]:
    """One run on the worker, and recovery's re-run (`registry.RESUMABLE`):
    `clone_links.run.run_clone_link`. Queued by this name, so a worker
    loading the job imports the routers first: the run module imports
    from them, and loaded on its own it would be a circular import."""
    return clone_link_run.run_clone_link(**params)


# --- Check and preview -----------------------------------------------------------


def _read(text: str) -> list[str]:
    """The pasted links, cleaned, each once. Raises 400: a refused paste
    (`CheckRefused`: every line that is not one post of a video site),
    nothing pasted, too many."""
    pasted = read_pasted(text)
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
    first. The page fills each tile as its line arrives. Nothing is read
    without Google Drive connected: `/run` would refuse it anyway."""
    if not refresh_token(AuthMiddleware.user(request).id):
        raise HTTPException(status_code=400, detail=NOT_CONNECTED)
    urls = _read("\n".join(body.urls))
    provider = body.provider or settings.aigen.default_provider
    price = _price(provider, _resolution(body.resolution or DEFAULT_RESOLUTION))
    found = preview_all(urls, AuthMiddleware.owner_id(request), price, THUMBS_PREFIX)
    return StreamingResponse(
        (p.model_dump_json() + "\n" for p in found),
        media_type="application/x-ndjson",
        headers=_UNBUFFERED,
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
    """One run's job, on the clone-links queue. The key goes to the worker
    only, never into the params its page shows. Not queued (Redis/RQ
    refused): the job is failed at once -- never left `running` for
    recovery to call a restart -- and the error raised."""
    job_id = create_job(TASK, params, actor=AuthMiddleware.actor(request))
    try:
        enqueue_to(
            CLONE_LINKS_QUEUE,
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
    except Exception:
        logger.warning("clone-links job %s could not be queued", job_id, exc_info=True)
        fail_job(job_id, NOT_QUEUED)
        raise
    return job_id


def _first_send(actor: str, batch: str) -> bool:
    """Whether this is `batch`'s first `/run` (atomic: of two racing sends,
    one). Redis down: True -- the batch's runs in the DB still stop a
    later resend, and queueing needs Redis anyway."""
    try:
        return bool(
            get_redis().set(
                f"clone-links:run:{actor}:{batch}", "1", nx=True, ex=_SENT_TTL_S
            )
        )
    except RedisError:
        logger.warning("could not mark batch %s as sent", batch, exc_info=True)
        return True


def _queued(jobs: list[Job], batch: str, user_id: int) -> CloneQueued:
    """`/run`'s answer: the runs' ids in paste order, their cards newest
    first like the gallery."""
    html = "".join(
        render_partial("_clone_links_card.html", job=card)
        for card in _cards(list(reversed(jobs)), user_id)
    )
    return CloneQueued(job_ids=[j.id for j in jobs], html=html, batch=batch)


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
    batch: Annotated[str | None, Form(pattern=BATCH)] = None,
) -> CloneQueued:
    """One run per ticked link; their job ids and cards, newest first like
    the gallery. Each link's video is downloaded by its own run. `batch`:
    the page's id of this Generate -- one already sent answers with its
    runs and queues nothing (none: a new id)."""
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

    actor = AuthMiddleware.actor(request)
    # Only now, every refusal behind it: a refused send leaves its batch
    # free for the corrected resend.
    if batch is not None:
        first = _first_send(actor, batch)
        sent = list_batch(TASK, batch, actor)
        if sent or not first:
            return _queued(sent, batch, user.id)
    batch = batch or uuid.uuid4().hex
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
        # batch's failed links (`recreate_links`), Stop stops them all.
        "batch": batch,
    }
    job_ids = [
        _queue(request, api_key.strip(), {**shared, "link": url}) for url in urls
    ]
    jobs = [job for j in job_ids if (job := get_job(j, actor)) is not None]
    return _queued(jobs, batch, user.id)


# --- Gallery and reviews -------------------------------------------------------


def _card(job: Job, review: CloneFromLink | None) -> CloneLinksCard:
    return CloneLinksCard.from_job(
        job,
        *job_assets(job, FILES_PREFIX),
        task_label=_export_label(job.params),
        poll_prefix=router.prefix,
        result_kind="video",
        review=review,
    )


def _cards(jobs: list[Job], user_id: int) -> list[CloneLinksCard]:
    """`user_id`'s runs as cards, each with its review -- only one of
    theirs, never another person's row under a reused job id."""
    # A run has one video: its review is file 0's.
    reviews = {
        r.job_id: r
        for r in clone_from_links.for_jobs([j.id for j in jobs], user_id=user_id)
        if r.file_index == 0
    }
    return [_card(j, reviews.get(j.id)) for j in jobs]


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
    cards = _cards(jobs, AuthMiddleware.scope(request))
    return cards, next_offset if next_offset < total else None


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
        # What is cached only: the page never waits on Google.
        drive_space=drive_space(user.id, ask=False),
        reel_mb=settings.clone_links.reel_mb,
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


def _states(jobs: list[Job], user_id: int) -> list[ReviewState]:
    return [
        ReviewState(
            job_id=card.job_id,
            settled=card.review_settled,
            html=render_partial("_clone_from_link.html", job=card),
        )
        for card in _cards(jobs, user_id)
    ]


def _own_runs(request: Request, job_ids: list[str]) -> list[Job]:
    actor = AuthMiddleware.actor(request)
    return [
        job
        for job_id in dict.fromkeys(job_ids)
        if (job := get_job(job_id, actor)) is not None and job.task == TASK
    ]


def _finishing(run: Job) -> bool:
    """A failed run recovery still finishes: its provider request was made
    (paid) and is remembered -- e.g. a poll that gave up while the provider
    was still generating."""
    return run.status == "failed" and tasks.recall(run.id) is not None


def _links_to_recreate(job: Job, actor: str) -> list[str]:
    """A failed run's: every failed run of its Generate, in paste order
    (ones hidden since left out, this one kept). A done run's: its own.
    Never a run recovery still finishes (nothing at all when it is this
    one), nor a link queued again since that is running or done -- an old
    card's Recreate never pays for a reel twice."""
    if _finishing(job):
        return []
    batch = job.params.get("batch")
    if job.status != "failed" or not batch:
        runs = [job]
    else:
        runs = [
            run
            for run in list_batch(TASK, batch, actor)
            if run.status == "failed"
            and (run.hidden_at is None or run.id == job.id)
            and not _finishing(run)
        ]
    if not runs:
        return []
    links = [run.params["link"] for run in runs]
    redone = redone_links(TASK, actor, links, min(run.created_at for run in runs))
    return [link for link in links if link not in redone]


@router.get("/recreate/{job_id}")
def recreate_links(request: Request, job_id: str) -> RecreateLinks:
    """The links a card's Recreate puts back in the box (none: nothing to
    redo)."""
    actor = AuthMiddleware.actor(request)
    job = get_job(job_id, actor)
    if job is None or job.task != TASK:
        raise HTTPException(status_code=404, detail="Not found")
    return RecreateLinks(links=_links_to_recreate(job, actor))


@router.post("/stop/{job_id}")
def stop(request: Request, job_id: str) -> BatchStopped:
    """Stop: the Generate of this card stopped (`clone_links.stop`). Rows
    are never failed or their requests forgotten here: a worker may hold a
    run, and a paid one is finished by its worker or by recovery."""
    actor = AuthMiddleware.actor(request)
    job = get_job(job_id, actor)
    batch = job.params.get("batch") if job is not None and job.task == TASK else None
    if not isinstance(batch, str) or not batch:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        stop_batch(batch)
    except RedisError as exc:
        logger.warning("clone-links batch %s not stopped", batch, exc_info=True)
        raise HTTPException(
            status_code=503, detail="Not stopped: press Stop again"
        ) from exc
    runs = list_batch(TASK, batch, actor)
    logger.info("clone-links batch %s stopped by %s", batch, actor)
    return BatchStopped(
        batch=batch, running=sum(run.status == "running" for run in runs)
    )


@router.get("/reviews")
def reviews(
    request: Request, job: Annotated[list[str], Query(max_length=200)]
) -> list[ReviewState]:
    """These runs' review blocks: what the page polls while reviews run."""
    return _states(_own_runs(request, job), AuthMiddleware.scope(request))


add_job_routes(
    router,
    LABEL,
    lambda _job: "Generating video… this can take a few minutes.",
    back_url="/clone-links",
    back_label="clone more",
    show_inputs=True,
)
