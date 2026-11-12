"""/download-assets "Download from Drive sheet": people add links from their
phones to a Google Sheet in the owner's Drive root (`settings.downloaders.
sheet_name`, found by name), one column per day with the date on top. A
click reads today's column and downloads every link not downloaded or
queued before, as one ordinary download job; a second click after more
links were added fetches only those. The button rests `sheet_cooldown_s`
after a click.

Nothing about the sheet's layout is fixed: every tab is looked at, the
column is the one whose first row holds today's date, the links are the
filled cells under it."""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import logfire
from pydantic import BaseModel
from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.stores import downloads as store
from ofmhelpers.db.stores import users
from ofmhelpers.features.downloads import media
from ofmhelpers.features.downloads.service import Links
from ofmhelpers.features.drive.folders import find_root, person_drive, root_name
from ofmhelpers.features.refusals import (
    NotFoundError,
    RefusedError,
    TooSoonError,
    TryAgainError,
    UnavailableError,
)
from ofmhelpers.integrations.downloaders.urls import NotALinkError, read_links
from ofmhelpers.integrations.gdrive import RECONNECT, DriveAuthError, DriveError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.downloads import SheetQueued
lazy from ofmhelpers.integrations.gdrive.models import DriveSheet

logger = get_logger(__name__)

NO_DRIVE = "Connect Google Drive on your Account page"
DRIVE_FAILED = "Google Drive did not answer. Try again."
BUSY = "Could not start the download. Try again."


class Column(BaseModel, frozen=True):
    """A day's column of the sheet: its links, top to bottom, each once, and
    how many filled cells were not a link."""

    links: list[str]
    skipped: int = 0


def today() -> date:
    """The day people mean by the sheet's dates: the Drive timezone's."""
    return datetime.now(ZoneInfo(settings.gdrive.timezone)).date()


def _cell_links(cell: date | str | None) -> list[str] | None:
    """The links of one cell (several lines: several links); none for an
    empty one, None for a cell holding something else."""
    if cell is None:
        return []
    if not isinstance(cell, str):
        return None
    try:
        return read_links(cell)
    except NotALinkError:
        return None


def column_of(sheet: DriveSheet, day: date) -> Column | None:
    """The column whose first row is `day`, in whichever tab has one (all of
    them, when several do); None when no tab has it."""
    found = False
    links: list[str] = []
    skipped = 0
    for tab in sheet.tabs:
        header = tab.rows[0] if tab.rows else []
        for index, top in enumerate(header):
            if top != day:
                continue
            found = True
            for row in tab.rows[1:]:
                read = _cell_links(row[index] if index < len(row) else None)
                if read is None:
                    skipped += 1
                else:
                    links.extend(read)
    return Column(links=list(dict.fromkeys(links)), skipped=skipped) if found else None


def _drive_owner(user_id: int) -> int:
    """Whose Drive holds the sheet: the person's own, a VA's employer's."""
    user = users.get_user(user_id)
    return user.owner_id if user and user.owner_id else user_id


def _read(user_id: int, day: date) -> Column:
    """Today's column of the person's sheet, read from Drive now."""
    name = settings.downloaders.sheet_name
    owner = _drive_owner(user_id)
    with logfire.span("drive sheet {sheet}", sheet=name, drive_owner=owner):
        try:
            drive = person_drive(owner, interactive=True)
            if drive is None:
                raise RefusedError(NO_DRIVE)
            root = find_root(drive, owner)
            file = drive.find_file(root, name) if root else None
            if file is None:
                msg = f"No sheet named {name} in {root_name()} in Google Drive"
                raise NotFoundError(msg)
            sheet = drive.sheet(file.id)
        except DriveAuthError as exc:
            raise RefusedError(RECONNECT) from exc
        except DriveError as exc:
            logger.warning("drive sheet %r of %s not read", name, owner, exc_info=True)
            raise UnavailableError(DRIVE_FAILED) from exc
    column = column_of(sheet, day)
    if column is None:
        msg = f"No column for {day:%d/%m/%Y} in {name}"
        raise NotFoundError(msg)
    return column


class Clicker(BaseModel, frozen=True):
    """One person's button: its rest between clicks and the links their
    earlier clicks queued, both kept in Redis."""

    user_id: int

    @property
    def _cooldown_key(self) -> str:
        return f"downloads:sheet-cooldown:{self.user_id}"

    def _claims_key(self, day: date) -> str:
        return f"downloads:sheet:{self.user_id}:{day.isoformat()}"

    def cooldown_left(self) -> int:
        """Seconds until the button works again; 0 = now. Redis down: 0 (the
        click itself then decides)."""
        try:
            left = int(get_redis().ttl(self._cooldown_key))
        except RedisError:
            logger.warning("sheet cooldown unreadable", exc_info=True)
            return 0
        return max(left, 0)

    def rest(self) -> None:
        """Starts the cooldown; `TooSoonError` while one is on. Redis down:
        allowed (a broken broker must not lock the button)."""
        try:
            redis = get_redis()
            seconds = settings.downloaders.sheet_cooldown_s
            if redis.set(self._cooldown_key, "1", nx=True, ex=seconds):
                return
            left = max(int(redis.ttl(self._cooldown_key)), 1)
        except RedisError:
            logger.warning("sheet cooldown unavailable, allowing", exc_info=True)
            return
        msg = f"Wait {left} s"
        raise TooSoonError(msg)

    def wake(self) -> None:
        """Ends the cooldown: the click was refused before it read any
        links, and the person may try again once they fixed what it named."""
        try:
            get_redis().delete(self._cooldown_key)
        except RedisError:
            logger.warning("sheet cooldown not ended", exc_info=True)

    def claim(self, day: date, links: list[str]) -> list[str]:
        """The links no earlier click queued, now marked as queued (a link
        whose job still runs has no `downloads` row to tell by). All or
        none: when Redis fails part-way, what this call marked is unmarked."""
        key = self._claims_key(day)
        fresh: list[str] = []
        try:
            redis = get_redis()
            for link in links:
                if redis.sadd(key, link):
                    # Not a comprehension: what was marked before a failure
                    # must be known to unmark it.
                    fresh.append(link)  # noqa: PERF401
            redis.expire(key, settings.downloaders.sheet_claim_s)
        except RedisError as exc:
            logger.warning("sheet links not claimed", exc_info=True)
            self.unclaim(day, fresh)
            raise TryAgainError(BUSY) from exc
        return fresh

    def unclaim(self, day: date, links: list[str]) -> None:
        if not links:
            return
        try:
            get_redis().srem(self._claims_key(day), *links)
        except RedisError:
            logger.warning(
                "%d sheet link(s) of %s stay claimed for user %s",
                len(links),
                day,
                self.user_id,
                exc_info=True,
            )


def cooldown_left(user_id: int) -> int:
    """Seconds until the person's button works again (the page's first draw)."""
    return Clicker(user_id=user_id).cooldown_left()


def start(actor: str, owner_id: int, assets_root: Path) -> SheetQueued:
    """The click: today's links not downloaded or queued before become one
    download job (none: no job). Raises the page's refusals: `TooSoonError`
    in the cooldown, `RefusedError` without Drive, `NotFoundError` without
    the sheet or today's column, `UnavailableError` when Drive failed. Only
    a click that read the day's links rests the button."""
    clicker = Clicker(user_id=owner_id)
    clicker.rest()
    day = today()
    try:
        column = _read(owner_id, day)
    except RefusedError, NotFoundError, UnavailableError:
        clicker.wake()
        raise
    have = {file.url for file in store.newest_of(owner_id, column.links)}
    fresh = clicker.claim(day, [link for link in column.links if link not in have])
    answer = SheetQueued(
        day=day,
        new=len(fresh),
        already=len(column.links) - len(fresh),
        skipped=column.skipped,
        cooldown_s=clicker.cooldown_left(),
    )
    if not fresh:
        return answer
    links = Links(
        task=media.TASK,
        run=media.run_downloads,
        actor=actor,
        owner_id=owner_id,
        assets_root=assets_root,
    )
    try:
        job_id = links.queue(fresh, sheet_date=day.isoformat())
    except Exception:
        clicker.unclaim(day, fresh)
        raise
    logger.info(
        "drive sheet: %d new link(s) of %s queued as %s", len(fresh), day, job_id
    )
    return answer.model_copy(update={"job_id": job_id})
