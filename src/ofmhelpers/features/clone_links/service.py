# Copyright (C) 2026 jpkrajewski

import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict
from redis.exceptions import RedisError
lazy from fastapi import UploadFile

from ofmhelpers.cache import QueueName, enqueue_to, get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.clone_ai_checks.schema import ReviewReason
from ofmhelpers.db.clone_from_links import store as clone_from_links
from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.db.jobs.store import (
    create_job,
    fail_job,
    get_job,
    list_batch,
    redone_links,
)
from ofmhelpers.db.users.store import refresh_token
from ofmhelpers.features.account.keys import Keys
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.manifests import build_ordered_paths
from ofmhelpers.features.clone.service import export_label, require_kind
from ofmhelpers.features.clone_links.constants import (
    CloneLinksTask,
    clone_links_constants,
)
from ofmhelpers.features.clone_links.links import read_pasted
from ofmhelpers.features.clone_links.models import (
    CheckRefused,
    LinkPreview,
    RecreateLinks,
)
from ofmhelpers.features.clone_links.preview import (
    Price,
    preview_all,
    previewed,
    thumb_file,
)
from ofmhelpers.features.clone_links.stop import stop_batch
from ofmhelpers.features.clone_pricing.pricing import UnpricedError, clone_price
from ofmhelpers.features.clone_pricing.targets import (
    DEFAULT_RESOLUTION,
    CloneTarget,
    clone_targets,
    offered_resolutions,
)
from ofmhelpers.features.jobs.models import CloneLinksCard
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import (
    NotFoundError,
    RefusedError,
    TryAgainError,
)
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.log import get_logger
from ofmhelpers.media.exports import exports
lazy from ofmhelpers.db.clone_ai_checks.schema import CloneFromLink

logger = get_logger(__name__)


class BatchStopped(BaseModel):
    batch: str
    running: int


class Sent(BaseModel):
    jobs: list[Job]
    batch: str


def read(text: str) -> list[str]:
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
        raise RefusedError(ReviewReason.NOT_CONNECTED)


def previews(
    urls: list[str],
    provider: str,
    resolution: str,
    user_id: int,
    owner_id: int,
) -> Iterator[LinkPreview]:
    _require_drive(user_id)
    links = read("\n".join(urls))
    provider = provider or settings.aigen.default_provider
    price = _price(provider, _resolution(resolution or DEFAULT_RESOLUTION))
    return preview_all(links, owner_id, price, clone_links_constants().thumbs_prefix)


def cover(key: str, owner_id: int) -> Path:
    valid = clone_links_constants().thumb_key.match(key)
    mine = bool(valid) and previewed(key, owner_id)
    path = thumb_file(key) if mine else None
    if path is None or not path.is_file():
        msg = "No cover"
        raise NotFoundError(msg)
    return path


def _first_send(actor: str, batch: str) -> bool:
    constants = clone_links_constants()
    try:
        return bool(
            get_redis().set(
                constants.run_key.format(actor=actor, batch=batch),
                "1",
                nx=True,
                ex=constants.sent_ttl_s,
            ),
        )
    except RedisError:
        logger.warning("could not mark batch %s as sent", batch, exc_info=True)
        return True


def _forget_send(actor: str, batch: str) -> None:
    try:
        get_redis().delete(
            clone_links_constants().run_key.format(actor=actor, batch=batch),
        )
    except RedisError:
        logger.warning("could not clear batch %s", batch, exc_info=True)


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
    links: list[str]
    batch: str | None


class _Shared(BaseModel, frozen=True):
    provider: str
    model: str
    prompt: str
    resolution: str
    audio: bool
    export: str
    mirror: bool


class Generate(BaseModel):
    model_config = ConfigDict(frozen=True)

    actor: str
    user_id: int
    owner_id: int
    assets_root: Path
    run: Callable[..., Any]

    def _queue(self, api_key: str, params: dict[str, Any]) -> str:
        job_id = create_job(CloneLinksTask.RUN, params, actor=self.actor)
        try:
            enqueue_to(
                QueueName.CLONE_LINKS,
                run_job,
                (
                    job_id,
                    self.run,
                    {
                        "job_id": job_id,
                        "api_key": api_key,
                        "assets_root": str(self.assets_root),
                        **params,
                    },
                ),
            )
        except Exception:
            logger.warning(
                "clone-links job %s could not be queued",
                job_id,
                exc_info=True,
            )
            fail_job(job_id, clone_links_constants().not_queued)
            raise
        return job_id

    def _key(self, form: Submitted) -> str:
        return Keys.of(self.owner_id).for_run(
            form.provider,
            _target(form.provider).model,
            form.api_key,
        )

    def _checked(self, form: Submitted) -> _Shared:
        target = _target(form.provider)
        if not self._key(form):
            msg = "API key is required"
            raise RefusedError(msg)
        if form.export not in exports():
            msg = "Unknown export"
            raise RefusedError(msg)
        resolution = _resolution(form.resolution)
        _require_drive(self.user_id)
        return _Shared(
            provider=form.provider,
            model=target.model,
            prompt=form.prompt,
            resolution=resolution,
            audio=form.audio,
            export=form.export,
            mirror=form.mirror,
        )

    def _subject(self, form: Submitted) -> list[str]:
        paths = build_ordered_paths(
            form.image_manifest,
            form.image,
            self.assets_root,
            user_id=self.owner_id,
        )
        max_images = settings.web.clone_max_images
        if not 1 <= len(paths) <= max_images:
            msg = f"Pick between 1 and {max_images} subject images"
            raise RefusedError(msg)
        require_kind(paths, "image", "The subject")
        return paths

    def start(self, form: Submitted) -> Sent:
        shared = self._checked(form).model_dump()
        urls = read("\n".join(form.links))
        images = self._subject(form)
        batch = form.batch
        sent: list[Job] = []
        if batch is not None:
            first = _first_send(self.actor, batch)
            sent = list_batch(CloneLinksTask.RUN, batch, self.actor)
            if not first:
                return Sent(jobs=sent, batch=batch)
        batch = batch or uuid.uuid4().hex
        shared = {**shared, "reference_images": images, "batch": batch}
        not_queued = clone_links_constants().not_queued
        queued = {job.params.get("link") for job in sent if job.error != not_queued}
        api_key = self._key(form)
        try:
            job_ids = [
                self._queue(api_key, {**shared, "link": url})
                for url in urls
                if url not in queued
            ]
        except Exception:
            _forget_send(self.actor, batch)
            raise
        jobs = [job for j in job_ids if (job := get_job(j, self.actor)) is not None]
        return Sent(jobs=[*sent, *jobs], batch=batch)


def card(job: Job, review: CloneFromLink | None) -> CloneLinksCard:
    constants = clone_links_constants()
    found = job_assets(job, constants.files_prefix)
    return CloneLinksCard.from_job(
        job,
        found.assets,
        found.failed_sources,
        {
            "task_label": export_label(job.params),
            "poll_prefix": constants.prefix,
            "result_kind": "video",
            "review": review,
        },
    )


def cards(jobs: list[Job], user_id: int) -> list[CloneLinksCard]:
    reviews = {
        r.job_id: r
        for r in clone_from_links.for_jobs([j.id for j in jobs], user_id=user_id)
        if r.file_index == 0
    }
    return [card(j, reviews.get(j.id)) for j in jobs]


def cards_of(job_ids: list[str], actor: str, user_id: int) -> list[CloneLinksCard]:
    runs = [
        job
        for job_id in dict.fromkeys(job_ids)
        if (job := get_job(job_id, actor)) is not None
        and job.task == CloneLinksTask.RUN
    ]
    return cards(runs, user_id)


def _own_run(job_id: str, actor: str) -> Job:
    job = get_job(job_id, actor)
    if job is None or job.task != CloneLinksTask.RUN:
        msg = "Not found"
        raise NotFoundError(msg)
    return job


def _finishing(run: Job) -> bool:
    return run.status == "failed" and tasks.recall(run.id) is not None


def _links_to_recreate(job: Job, actor: str) -> list[str]:
    if _finishing(job):
        return []
    batch = job.params.get("batch")
    if job.status != "failed" or not batch:
        runs = [job]
    else:
        runs = [
            run
            for run in list_batch(CloneLinksTask.RUN, batch, actor)
            if run.status == "failed"
            and (run.hidden_at is None or run.id == job.id)
            and not _finishing(run)
        ]
    if not runs:
        return []
    links = [run.params["link"] for run in runs]
    since = min(run.created_at for run in runs)
    redone = redone_links(CloneLinksTask.RUN, actor, links, since)
    return [link for link in links if link not in redone]


def recreate(job_id: str, actor: str) -> RecreateLinks:
    return RecreateLinks(links=_links_to_recreate(_own_run(job_id, actor), actor))


def stop(job_id: str, actor: str) -> BatchStopped:
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
    runs = list_batch(CloneLinksTask.RUN, batch, actor)
    logger.info("clone-links batch %s stopped by %s", batch, actor)
    return BatchStopped(
        batch=batch,
        running=sum(run.status == "running" for run in runs),
    )
