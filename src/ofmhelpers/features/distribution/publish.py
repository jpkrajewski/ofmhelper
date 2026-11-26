# Copyright (C) 2026 jpkrajewski

import shutil
import time
from pathlib import Path

import logfire
from PIL import Image
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.distribution import store
from ofmhelpers.db.distribution.schema import Channel, Post, PostKind, PostStatus
from ofmhelpers.features.assets.store import uploads_root
from ofmhelpers.features.distribution.presets import preset_of
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.features.storage.client import storage_of
from ofmhelpers.integrations.pinterest import (
    Media,
    PinDraft,
    PinterestClient,
    PinterestLoggedOutError,
    PinterestMediaError,
    PinterestRateLimitedError,
    session_expires_at,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media import trim, video_probe
lazy from ofmhelpers.features.storage.service import StorageServiceReview

logger = get_logger(__name__)

TASK = "pinterest"
NO_POST = "No such post"
NOT_CLAIMED = "This post is not being pinned (it was retried meanwhile)"
NO_MODEL = "This file's model has no preset"


class PublishedPin(BaseModel):
    post_id: int
    pin_id: str
    url: str


def pin_url(pin_id: str) -> str:
    return f"https://www.pinterest.com/pin/{pin_id}/"


def publish_post(*, post_id: int, job_id: str = "") -> PublishedPin:  # noqa: ARG001
    repo = store.repository()
    post = repo.post(post_id)
    if post is None:
        raise NotFoundError(NO_POST)
    if post.status is PostStatus.published and post.remote_id:
        return PublishedPin(
            post_id=post.id,
            pin_id=post.remote_id,
            url=pin_url(post.remote_id),
        )
    if post.status is not PostStatus.publishing:
        raise NotFoundError(NOT_CLAIMED)
    channel = repo.channel(post.channel_id)
    if channel is None:
        raise NotFoundError(NO_POST)
    with logfire.span(
        "pinterest pin @{account}",
        account=channel.account_name,
        channel_id=channel.id,
        model=post.preset,
        post_id=post.id,
        file=post.name,
        kind=post.kind,
        source=post.source,
        attempt=post.attempts,
    ) as span:
        review = storage_of(channel.user_id, interactive=False).review
        pin = _publish(channel, post, review)
        span.set_attribute("pin_id", pin.pin_id)
    return pin


def _publish(
    channel: Channel,
    post: Post,
    review: StorageServiceReview,
) -> PublishedPin:
    repo = store.repository()
    model = preset_of(channel.user_id, post.preset)
    if model is None:
        repo.failed(post.id, NO_MODEL)
        raise NotFoundError(NO_MODEL)
    here = uploads_root() / "distribution" / str(post.id)
    start = time.monotonic()
    try:
        path = _download(review, post, here)
        client = PinterestClient(repo.cookies(channel.id))
        renewed = client
        media = _media(client, path, post.kind, here)
        board = client.board(model.board)
        pin = client.create_pin(
            PinDraft(
                board_id=board.id,
                media=media,
                title=post.title if post.title is not None else model.title,
                description=post.description or "",
                link=post.link or "",
            ),
        )
    except PinterestRateLimitedError as exc:
        wait_s = exc.retry_after or settings.distribution.rate_limit_pause_s
        repo.requeue(post.id, str(exc))
        repo.set_resume(channel.id, time.time() + wait_s)
        logger.warning(
            "pinterest @%s: rate limited on %s, account waits %.0f s",
            channel.account_name,
            post.name,
            wait_s,
        )
        raise
    except PinterestLoggedOutError as exc:
        repo.set_error(channel.id, str(exc))
        repo.failed(post.id, str(exc))
        logger.exception(
            "pinterest @%s: logged out, account stopped until new cookies",
            channel.account_name,
        )
        raise
    except PinterestMediaError as exc:
        repo.failed(post.id, str(exc), final=True)
        logger.warning(
            "pinterest @%s: %s cannot be pinned (not tried again): %s",
            channel.account_name,
            post.name,
            exc,
        )
        raise
    except Exception as exc:
        repo.failed(post.id, str(exc) or type(exc).__name__)
        logger.warning(
            "pinterest @%s: %s failed (try %s): %s",
            channel.account_name,
            post.name,
            post.attempts,
            exc,
        )
        _pause_if_failing(channel)
        raise
    finally:
        shutil.rmtree(here, ignore_errors=True)
    repo.published(post.id, pin.id)
    _keep_session(channel, renewed)
    logger.info(
        "pinterest @%s: pinned %s %s in %.1f s -> %s",
        channel.account_name,
        post.kind,
        post.name,
        time.monotonic() - start,
        pin.url,
    )
    return PublishedPin(post_id=post.id, pin_id=pin.id, url=pin.url)


def _pause_if_failing(channel: Channel) -> None:
    n = settings.distribution.failures_before_pause
    if store.repository().failed_in_a_row(channel.id, n):
        wait_s = settings.distribution.rate_limit_pause_s
        store.repository().set_resume(channel.id, time.time() + wait_s)
        logger.warning(
            "pinterest @%s: last %s pins failed, account waits %.0f s",
            channel.account_name,
            n,
            wait_s,
        )


def _keep_session(channel: Channel, client: PinterestClient) -> None:
    try:
        cookies = client.cookies_txt()
        store.repository().set_cookies(channel.id, cookies, session_expires_at(cookies))
    except Exception:
        logger.exception(
            "pinterest @%s: could not save the renewed session",
            channel.account_name,
        )


def _download(review: StorageServiceReview, post: Post, here: Path) -> Path:
    got = review.target().drive.download(post.file_id)
    here.mkdir(parents=True, exist_ok=True)
    path = here / f"media{Path(got.name).suffix.lower() or '.bin'}"
    path.write_bytes(got.content)
    return path


def _media(client: PinterestClient, path: Path, kind: PostKind, here: Path) -> Media:
    timeout_s = settings.distribution.ffmpeg_timeout_s
    if kind is PostKind.image:
        with Image.open(path) as im:
            width, height = im.size
        upload_id = client.upload(path)
        return Media(signature=client.processed(upload_id), width=width, height=height)
    size = video_probe.video_size(path, timeout_s)
    duration_ms = int(video_probe.duration_s(path, timeout_s) * 1000)
    cover = here / "cover.jpg"
    cover.write_bytes(
        trim.frame(
            path,
            0,
            short_side=min(size.width, size.height),
            threads=1,
            timeout_s=timeout_s,
        ),
    )
    video_id = client.upload(path, video_ms=duration_ms)
    cover_id = client.upload(cover)
    return Media(
        signature=client.processed(video_id),
        cover_signature=client.processed(cover_id),
        width=size.width,
        height=size.height,
        video=True,
    )
