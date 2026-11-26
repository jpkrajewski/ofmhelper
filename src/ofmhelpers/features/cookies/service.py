# Copyright (C) 2026 jpkrajewski

from typing import BinaryIO
lazy from collections.abc import Callable

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.config.retired import retired
from ofmhelpers.db.cookies import store as cookies
from ofmhelpers.db.cookies.schema import SavedCookies
from ofmhelpers.db.jobs.store import create_job
from ofmhelpers.features.cookies import jar
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.reels.watch import unlock_gated
from ofmhelpers.features.refusals import RefusedError

UNLOCK_TASK = "reels_unlock"

_NOTES = {
    "uploaded": "Cookies saved. Your accounts "
    "synced without a login are syncing again.",
    "removed": "Cookies removed. Your downloads and scans now run logged out.",
}


class PageView(BaseModel):
    saved: SavedCookies | None
    message: str | None
    error: str | None


def page_view(
    owner_id: int,
    note: str | None = None,
    error: str | None = None,
) -> PageView:
    return PageView(
        saved=jar.saved(owner_id),
        message=_NOTES.get(note or ""),
        error=error,
    )


def _after_upload(user_id: int, rescan_limited: Callable[[int], object]) -> None:
    if not retired().has("reels"):
        job_id = create_job(UNLOCK_TASK, {}, actor=str(user_id))
        enqueue(run_job, (job_id, unlock_gated, {"user_id": user_id}))
    rescan_limited(user_id)


def upload(
    owner_id: int,
    file: BinaryIO,
    name: str | None,
    size: int | None,
    rescan_limited: Callable[[int], object],
) -> None:
    try:
        if size is not None:
            jar.check_size(size)
        jar.save(owner_id, file.read(), (name or "cookies.txt")[:255])
    except jar.CookieFileError as exc:
        raise RefusedError(str(exc)) from exc
    _after_upload(owner_id, rescan_limited)


def remove(owner_id: int) -> None:
    cookies.remove(owner_id)
