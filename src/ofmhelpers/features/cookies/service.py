from typing import BinaryIO

from pydantic import BaseModel

from ofmhelpers.auth.access import Area, retired
from ofmhelpers.cache import enqueue
from ofmhelpers.db.stores import cookies
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.accounts.tracker import rescan_limited
from ofmhelpers.features.cookies import jar
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.reels.watch import unlock_gated
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.schemas import SavedCookies

UNLOCK_TASK = "reels_unlock"

_NOTES = {
    "uploaded": "Cookies saved. Your accounts synced without a login are syncing again.",
    "removed": "Cookies removed. Your downloads and scans now run logged out.",
}


class PageView(BaseModel):
    saved: SavedCookies | None
    message: str | None
    error: str | None


def page_view(
    owner_id: int, note: str | None = None, error: str | None = None
) -> PageView:
    return PageView(
        saved=jar.saved(owner_id), message=_NOTES.get(note or ""), error=error
    )


def _after_upload(user_id: int) -> None:
    if not retired(Area.REELS):
        job_id = create_job(UNLOCK_TASK, {}, actor=str(user_id))
        enqueue(run_job, job_id, unlock_gated, {"user_id": user_id})
    rescan_limited(user_id)


def upload(owner_id: int, file: BinaryIO, name: str | None, size: int | None) -> None:
    try:
        if size is not None:
            jar.check_size(size)
        jar.save(owner_id, file.read(), (name or "cookies.txt")[:255])
    except jar.CookieFileError as exc:
        raise RefusedError(str(exc)) from exc
    _after_upload(owner_id)


def remove(owner_id: int) -> None:
    cookies.remove(owner_id)
