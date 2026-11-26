# Copyright (C) 2026 jpkrajewski

import random
import time
from itertools import groupby

from pydantic import BaseModel

from ofmhelpers.cache.queue import active_job_ids
from ofmhelpers.config import settings
from ofmhelpers.db.distribution import store
from ofmhelpers.features.distribution.links import model_link
from ofmhelpers.features.distribution.presets import preset_of, presets_of
from ofmhelpers.features.distribution.publish import TASK, publish_post
from ofmhelpers.features.distribution.sources import Sources
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.features.storage.client import storage_of
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.distribution.schema import Channel, ModelPreset
lazy from ofmhelpers.features.storage.service import StorageService

logger = get_logger(__name__)

CUT_OFF = "Cut off (the server restarted while it was being pinned); tried again"
_DAY_S = 86400
_HANDLE_PREFIXES = ("ig:", "insta:")


def slot_s() -> float:
    return _DAY_S / settings.distribution.pins_per_day


def run_round() -> None:
    s = settings.distribution
    alive = active_job_ids()
    if alive is not None:
        cut = store.repository().cut_off(s.job_timeout_s + 60, CUT_OFF, alive)
        if cut:
            logger.warning("distribution: %s pin(s) cut off by a restart, retried", cut)
    channels = sorted(store.repository().running(), key=lambda c: c.user_id)
    if settings.gdrive.sweeping(settings.infra.production):
        for user_id, mine in groupby(channels, key=lambda c: c.user_id):
            storage = storage_of(user_id, interactive=False)
            _fill(_sources(user_id, storage), list(mine))
    if not s.scheduled(settings.infra.production):
        return
    started = limited = 0
    for channel in channels:
        try:
            state = _schedule(channel)
        except Exception:
            logger.exception("distribution: scheduling @%s", channel.account_name)
            continue
        started += state == "started"
        limited += state == "limited"
    logger.info(
        "distribution round: %s account(s), %s pin(s) started, %s rate limited",
        len(channels),
        started,
        limited,
    )


def _schedule(channel: Channel) -> str:
    now = time.time()
    fresh = store.repository().channel(channel.id)
    if fresh is None or fresh.error or fresh.paused:
        return ""
    if fresh.resume_at is not None and fresh.resume_at > now:
        return "limited"
    if not _due(fresh, now):
        return ""
    store.repository().set_next(fresh.id, _after(now))
    return "started" if start_next(fresh) is not None else ""


def _sources(user_id: int, storage: StorageService) -> Sources:
    return Sources(user_id=user_id, folders=storage.folders, review=storage.review)


def fill_many(storage: StorageService, channels: list[Channel]) -> int:
    return _fill(_sources(storage.review.owner_id, storage), channels)


def _fill(sources: Sources, channels: list[Channel]) -> int:
    try:
        cards = sources.cards()
        files = [
            f for m in presets_of(sources.user_id) for f in sources.of_model(m, cards)
        ]
    except Exception:
        logger.exception("distribution: content of user %s", sources.user_id)
        return 0
    added = 0
    for channel in channels:
        try:
            new = store.repository().add_posts(channel.id, files)
        except Exception:
            logger.exception("distribution: queueing for @%s", channel.account_name)
            continue
        if new:
            logger.info(
                "distribution: @%s queued %s new file(s)",
                channel.account_name,
                new,
            )
        added += new
    return added


def _due(channel: Channel, now: float) -> bool:
    repo = store.repository()
    if channel.next_at is None:
        first = now + random.uniform(0, slot_s())  # noqa: S311
        repo.set_next(channel.id, first)
        logger.info(
            "distribution: @%s first pin in %.0f min",
            channel.account_name,
            (first - now) / 60,
        )
        return False
    return now >= channel.next_at and not repo.publishing(channel.id)


def _after(now: float) -> float:
    j = settings.distribution.jitter
    return now + slot_s() * random.uniform(1 - j, 1 + j)  # noqa: S311


class _Pin(BaseModel, frozen=True):
    title: str
    caption: str
    link: str


def _what(model: ModelPreset, link: str) -> _Pin:
    pick = random.random
    if model.instagram and pick() < settings.distribution.handle_share:
        prefix = random.choice(_HANDLE_PREFIXES)  # noqa: S311
        title = f"{prefix} {random.choice(model.instagram)}"  # noqa: S311
        return _Pin(title=title, caption="", link="")
    return _Pin(
        title=model.title,
        caption=random.choice(model.descriptions),  # noqa: S311
        link=link,
    )


def start_next(channel: Channel) -> str | None:
    repo = store.repository()
    post = repo.next_post(channel, settings.distribution.max_attempts)
    if post is None:
        logger.info("distribution: @%s has nothing left to pin", channel.account_name)
        return None
    model = preset_of(channel.user_id, post.preset)
    if model is None:
        logger.error(
            "distribution: %s's model %r has no preset: not pinned",
            post.name,
            post.preset,
        )
        return None
    try:
        link = model_link(channel.user_id, model)
    except RefusedError:
        logger.exception("distribution: %s has no link; pinning it without", model.key)
        link = ""
    pin = _what(model, link)
    if not repo.claim(post.id, pin.title, pin.caption, pin.link):
        return None
    try:
        job_id = start_job(
            TASK,
            {"batch": str(channel.id), "post_id": post.id, "file": post.name},
            publish_post,
            {"post_id": post.id},
            actor=str(channel.user_id),
            job_timeout=settings.distribution.job_timeout_s,
        )
    except Exception as exc:
        repo.failed(post.id, f"Could not be queued: {exc}")
        raise
    repo.set_job(post.id, job_id)
    logger.info(
        "distribution: @%s pinning %s %s of %s as %r (job %s)",
        channel.account_name,
        post.kind,
        post.name,
        model.key,
        pin.title,
        job_id,
    )
    return job_id
