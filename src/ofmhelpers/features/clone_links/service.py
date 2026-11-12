"""What /clone-links does behind its routes
(web/routers/generation/clone_links.py): the paste read and refused, the
previews priced, a Generate queued once, the cards, Recreate's links, Stop.

A Generate is one `batch` (the page's id for it, sent with `/run`): a resend
of the same batch -- the answer lost, a retry after an error -- returns the
runs already queued and pays for nothing again."""

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError
lazy from fastapi import UploadFile

from ofmhelpers.cache import QueueName, enqueue_to, get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.stores import clone_from_links
from ofmhelpers.db.stores.jobs import (
    create_job,
    fail_job,
    get_job,
    list_batch,
    redone_links,
)
from ofmhelpers.db.stores.users import refresh_token
from ofmhelpers.features.account.keys import Keys
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.manifests import build_ordered_paths
from ofmhelpers.features.clone.pricing import UnpricedError, clone_price
from ofmhelpers.features.clone.service import export_label, require_kind
from ofmhelpers.features.clone.targets import (
    DEFAULT_RESOLUTION,
    CloneTarget,
    clone_targets,
    offered_resolutions,
)
from ofmhelpers.features.clone_links.links import read_pasted
from ofmhelpers.features.clone_links.models import (
    CheckRefused,
    LinkPreview,
    RecreateLinks,
)
from ofmhelpers.features.clone_links.preview import (
    THUMB_KEY,
    Price,
    preview_all,
    previewed,
    thumb_file,
)
from ofmhelpers.features.clone_links.review import NOT_CONNECTED
from ofmhelpers.features.clone_links.stop import stop_batch
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import (
    NotFoundError,
    RefusedError,
    TryAgainError,
)
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.log import get_logger
from ofmhelpers.media.exports import exports
from ofmhelpers.schemas import CloneLinksCard, Job
lazy from ofmhelpers.schemas.clone_ai_checks import CloneFromLink

logger = get_logger(__name__)

TASK = "clone_links"
LABEL = "Clone links"
PREFIX = "/clone-links"
FILES_PREFIX = f"{PREFIX}/files"
THUMBS_PREFIX = f"{PREFIX}/thumb"
# A Generate's id, made by the page (`/run`'s `batch`).
BATCH = r"^[0-9a-f]{8,32}$"
# How long a sent batch is remembered as sent (a resend within it, even one
# racing the first, queues nothing; after it, its runs still answer).
_SENT_TTL_S = 24 * 3600
# A run's row made but never queued (Redis/RQ refused): its card says so.
NOT_QUEUED = "Not queued: run it again"


class BatchStopped(BaseModel):
    """`POST /stop/{job_id}`: the Generate stopped, and how many of its runs
    are still running (a paid one finishes; the rest fail "Stopped" as the
    worker reaches them)."""

    batch: str
    running: int


class Sent(BaseModel):
    """A Generate's runs in paste order, under its id."""

    jobs: list[Job]
    batch: str


# --- Check and preview -----------------------------------------------------------


def read(text: str) -> list[str]:
    """The pasted links, cleaned, each once. Refused: a paste with a line
    that is not one post of a video site (`CheckRefused`: every such line),
    nothing pasted, too many."""
    pasted = read_pasted(text)
    if pasted.bad_lines:
        msg = "Please put one link per line"
        raise RefusedError(msg, CheckRefused(message=msg, lines=pasted.bad_lines))
    if not pasted.links:
        msg = "Paste at least one link"
        raise RefusedError(msg)
    cap = settings.clone_links.max_links
    if len(pasted.links) > cap:
        msg = f"At most {cap} links at a time ({len(pasted.links)} pasted)"
        raise RefusedError(msg)
    return pasted.links


def _target(provider: str) -> CloneTarget:
    target = clone_targets().get(provider or settings.aigen.default_provider)
    if target is None:
        msg = "That provider cannot clone"
        raise RefusedError(msg)
    return target


def _resolution(resolution: str) -> str:
    if resolution.upper() not in offered_resolutions():
        msg = "Unknown resolution"
        raise RefusedError(msg)
    return resolution.upper()


def _price(provider: str, resolution: str) -> Price:
    """A source second's price at `provider` and `resolution` (what /clone's
    panel charges), and the longest video it takes."""
    target = _target(provider)
    try:
        price = clone_price(provider)
        credits_per_s = price.per_second(resolution)
    except UnpricedError as exc:
        msg = "Unknown resolution"
        raise RefusedError(msg) from exc
    return Price(
        credits_per_s=credits_per_s,
        usd_per_credit=price.usd_per_credit,
        max_s=target.max_reference_s,
    )


def _require_drive(user_id: int) -> None:
    if not refresh_token(user_id):
        raise RefusedError(NOT_CONNECTED)


def previews(
    urls: list[str], provider: str, resolution: str, user_id: int, owner_id: int
) -> Iterator[LinkPreview]:
    """Generate's dialog: every link's cover, length and price, read without
    downloading (as the clicker, with their cookies.txt), all in parallel,
    each as it is done, cached ones first. Nothing is read without Google
    Drive connected: `/run` would refuse it anyway. Every refusal is raised
    here, before the first preview."""
    _require_drive(user_id)
    links = read("\n".join(urls))
    provider = provider or settings.aigen.default_provider
    price = _price(provider, _resolution(resolution or DEFAULT_RESOLUTION))
    return preview_all(links, owner_id, price, THUMBS_PREFIX)


def cover(key: str, owner_id: int) -> Path:
    """A previewed link's cover, kept small on disk (`preview`): only a file
    already made, never a fetch, and only of a link `owner_id` previewed
    (the kept file is shared, who may fetch is not): somebody else's
    answers like one that is not there."""
    mine = bool(THUMB_KEY.match(key)) and previewed(key, owner_id)
    path = thumb_file(key) if mine else None
    if path is None or not path.is_file():
        msg = "No cover"
        raise NotFoundError(msg)
    return path


# --- Generate ------------------------------------------------------------------


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


def _forget_send(actor: str, batch: str) -> None:
    """`batch`'s send failed halfway: its resend counts as a first send."""
    try:
        get_redis().delete(f"clone-links:run:{actor}:{batch}")
    except RedisError:
        logger.warning("could not clear batch %s", batch, exc_info=True)


class Submitted(Protocol):
    """The /clone-links form as its route read it (`CloneLinksForm`)."""

    provider: str
    api_key: str
    export: str
    resolution: str
    prompt: str
    audio: bool
    mirror: bool
    image: list[UploadFile]
    image_manifest: str
    links: list[str]
    batch: str | None


class Generate(BaseModel):
    """One /clone-links submit: whose it is, and `run`, the worker function
    its router owns (RQ stores a job's function by its path)."""

    model_config = ConfigDict(frozen=True)

    actor: str
    user_id: int
    owner_id: int
    assets_root: Path
    run: Callable[..., Any]

    def _queue(self, api_key: str, params: dict[str, Any]) -> str:
        """One run's job, on the clone-links queue. The key goes to the worker
        only, never into the params its page shows. Not queued (Redis/RQ
        refused): the job is failed at once -- never left `running` for
        recovery to call a restart -- and the error raised."""
        job_id = create_job(TASK, params, actor=self.actor)
        try:
            enqueue_to(
                QueueName.CLONE_LINKS,
                run_job,
                job_id,
                self.run,
                {
                    "job_id": job_id,
                    "api_key": api_key,
                    "assets_root": str(self.assets_root),
                    **params,
                },
            )
        except Exception:
            logger.warning(
                "clone-links job %s could not be queued", job_id, exc_info=True
            )
            fail_job(job_id, NOT_QUEUED)
            raise
        return job_id

    def _key(self, form: Submitted) -> str:
        """The key the runs go out on: the owner's for the target's model
        (`Keys.for_run`); "" without one."""
        return Keys.of(self.owner_id).for_run(
            form.provider, _target(form.provider).model, form.api_key
        )

    def _checked(self, form: Submitted) -> dict[str, Any]:
        """What every run of this Generate shares; every refusal is raised
        here, before anything is marked sent or queued."""
        target = _target(form.provider)
        if not self._key(form):
            msg = "API key is required"
            raise RefusedError(msg)
        if form.export not in exports():
            msg = "Unknown export"
            raise RefusedError(msg)
        resolution = _resolution(form.resolution)
        _require_drive(self.user_id)
        return {
            "provider": form.provider,
            "model": target.model,
            "prompt": form.prompt,
            "resolution": resolution,
            "audio": form.audio,
            "export": form.export,
            "mirror": form.mirror,
        }

    def _subject(self, form: Submitted) -> list[str]:
        paths = build_ordered_paths(
            form.image_manifest, form.image, self.assets_root, user_id=self.owner_id
        )
        max_images = settings.web.clone_max_images
        if not 1 <= len(paths) <= max_images:
            msg = f"Pick between 1 and {max_images} subject images"
            raise RefusedError(msg)
        require_kind(paths, "image", "The subject")
        return paths

    def start(self, form: Submitted) -> Sent:
        """One run per ticked link. Each link's video is downloaded by its
        own run. `form.batch`: the page's id of this Generate -- one already
        sent answers with its runs and queues nothing (none: a new id)."""
        shared = self._checked(form)
        urls = read("\n".join(form.links))
        images = self._subject(form)
        # Only now, every refusal behind it: a refused send leaves its batch
        # free for the corrected resend.
        batch = form.batch
        sent: list[Job] = []
        if batch is not None:
            first = _first_send(self.actor, batch)
            sent = list_batch(TASK, batch, self.actor)
            if not first:
                return Sent(jobs=sent, batch=batch)
        batch = batch or uuid.uuid4().hex
        # `batch`, one per Generate: a failed run's Recreate brings back its
        # batch's failed links (`recreate`), Stop stops them all.
        shared = {**shared, "reference_images": images, "batch": batch}
        queued = {job.params.get("link") for job in sent if job.error != NOT_QUEUED}
        api_key = self._key(form)
        try:
            job_ids = [
                self._queue(api_key, {**shared, "link": url})
                for url in urls
                if url not in queued
            ]
        except Exception:
            # Some may be queued already: the page's resend gets those back and
            # queues only the rest, never one twice.
            _forget_send(self.actor, batch)
            raise
        jobs = [job for j in job_ids if (job := get_job(j, self.actor)) is not None]
        return Sent(jobs=[*sent, *jobs], batch=batch)


# --- Cards, Recreate, Stop -----------------------------------------------------


def card(job: Job, review: CloneFromLink | None) -> CloneLinksCard:
    found = job_assets(job, FILES_PREFIX)
    return CloneLinksCard.from_job(
        job,
        found.assets,
        found.failed_sources,
        task_label=export_label(job.params),
        poll_prefix=PREFIX,
        result_kind="video",
        review=review,
    )


def cards(jobs: list[Job], user_id: int) -> list[CloneLinksCard]:
    """`user_id`'s runs as cards, each with its review -- only one of
    theirs, never another person's row under a reused job id."""
    # A run has one video: its review is file 0's.
    reviews = {
        r.job_id: r
        for r in clone_from_links.for_jobs([j.id for j in jobs], user_id=user_id)
        if r.file_index == 0
    }
    return [card(j, reviews.get(j.id)) for j in jobs]


def cards_of(job_ids: list[str], actor: str, user_id: int) -> list[CloneLinksCard]:
    """The cards of the asker's own runs among `job_ids`, each once."""
    runs = [
        job
        for job_id in dict.fromkeys(job_ids)
        if (job := get_job(job_id, actor)) is not None and job.task == TASK
    ]
    return cards(runs, user_id)


def _own_run(job_id: str, actor: str) -> Job:
    job = get_job(job_id, actor)
    if job is None or job.task != TASK:
        msg = "Not found"
        raise NotFoundError(msg)
    return job


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


def recreate(job_id: str, actor: str) -> RecreateLinks:
    """The links a card's Recreate puts back in the box (none: nothing to
    redo)."""
    return RecreateLinks(links=_links_to_recreate(_own_run(job_id, actor), actor))


def stop(job_id: str, actor: str) -> BatchStopped:
    """Stop: the Generate of this card stopped (`stop.py`). Rows are never
    failed or their requests forgotten here: a worker may hold a run, and a
    paid one is finished by its worker or by recovery."""
    batch = _own_run(job_id, actor).params.get("batch")
    if not isinstance(batch, str) or not batch:
        msg = "Not found"
        raise NotFoundError(msg)
    try:
        stop_batch(batch)
    except RedisError as exc:
        logger.warning("clone-links batch %s not stopped", batch, exc_info=True)
        msg = "Not stopped: press Stop again"
        raise TryAgainError(msg) from exc
    runs = list_batch(TASK, batch, actor)
    logger.info("clone-links batch %s stopped by %s", batch, actor)
    return BatchStopped(
        batch=batch, running=sum(run.status == "running" for run in runs)
    )
