"""
ofmhelpers/web/drive_folders.py

/drive's folders in the shared Google Drive folder (`settings.gdrive`):
every Instagram login on an active phone gets "<handle> - <owner>" in the
root, and inside it one folder per day ("Oct 21", the day in
`settings.gdrive.timezone`), for the VA to upload that day's content into.

The sweeper (`drive_loop`, every `check_interval_s`) asks the database
what is missing for today and calls Drive only for that, so a quiet day
costs no request. Folders are found by name before one is made: a folder
made by hand is reused, a re-run never duplicates. Once known, a folder is
kept by id: a new owner renames it. Nothing in Drive is ever moved or
deleted. One sweep at a time (a Redis lock): the page's Sync button and the
loop can overlap.

`board(day)` is what /drive shows: each login's day folder listed live
from Drive, in parallel (`list_workers`).
"""

from __future__ import annotations

import asyncio
import datetime
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.gdrive import (
    Drive,
    DriveError,
    DriveFile,
    FolderGoneError,
    connect,
    service_account_email,
)
from ofmhelpers.log import get_logger
from ofmhelpers.web.schemas import (
    DriveBoard,
    DriveCard,
    DriveTarget,
    StoredFolder,
    SyncReport,
    day_folder_name,
)
from ofmhelpers.web.stores import drive_folders as store

logger = get_logger(__name__)

_LOCK_KEY = "lock:drive:sync"
# Longer than any sweep; a crashed one frees the lock by itself.
_LOCK_TTL_S = 600


def today() -> datetime.date:
    return datetime.datetime.now(ZoneInfo(settings.gdrive.timezone)).date()


def _missing(
    targets: list[DriveTarget],
    folders: dict[int, StoredFolder],
    days: dict[int, str],
) -> list[DriveTarget]:
    """The logins with no folder, a folder under an old name, or no folder
    for the day."""
    return [
        t
        for t in targets
        if t.login_id not in days
        or t.login_id not in folders
        or folders[t.login_id].name != t.folder_name
    ]


def _account_folder(
    drive: Drive,
    root: str,
    target: DriveTarget,
    stored: StoredFolder | None,
    report: SyncReport,
) -> tuple[str, bool]:
    """(folder id, whether it is the one stored): the stored folder, renamed
    when the name is old; else one of that name in the root; else a new
    one."""
    if stored is not None:
        try:
            current = drive.folder(stored.folder_id)
        except FolderGoneError:
            logger.warning(
                "drive: folder of %s is gone, finding or making it again",
                target.handle,
            )
            store.forget_account_folder(target.login_id)
        else:
            if current.name != target.folder_name:
                drive.rename(current.id, target.folder_name)
                report.renamed.append(target.folder_name)
            if stored.name != target.folder_name:
                store.save_account_folder(
                    target.login_id, current.id, target.folder_name
                )
            return current.id, True
    found = drive.find_folder(root, target.folder_name)
    if found is not None:
        report.adopted.append(target.folder_name)
    else:
        found = drive.create_folder(root, target.folder_name)
        report.created.append(target.folder_name)
    store.save_account_folder(target.login_id, found.id, target.folder_name)
    return found.id, False


def _sync_one(
    drive: Drive,
    root: str,
    target: DriveTarget,
    stored: StoredFolder | None,
    has_day: bool,
    day: datetime.date,
    report: SyncReport,
) -> None:
    folder_id, kept = _account_folder(drive, root, target, stored, report)
    if has_day and kept:
        return
    made = drive.ensure_folder(folder_id, day_folder_name(day))
    store.save_day_folder(target.login_id, day, made.id)
    report.days += 1


def sync(day: datetime.date | None = None) -> SyncReport:
    """Makes whatever `day` (default today) is missing. Raises `DriveError`
    when Drive is not set up or the root is not shared; one login failing
    is reported, the others still go."""
    day = day or today()
    report = SyncReport()
    redis = get_redis()
    if not redis.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL_S):
        report.busy = True
        return report
    try:
        folders = store.account_folders()
        days = store.day_folders(day)
        todo = _missing(store.targets(), folders, days)
        if not todo:
            return report
        drive = connect()
        root = settings.gdrive.root_folder_id
        drive.folder(root)
        for target in todo:
            try:
                _sync_one(
                    drive,
                    root,
                    target,
                    folders.get(target.login_id),
                    target.login_id in days,
                    day,
                    report,
                )
            except DriveError as exc:
                logger.warning("drive: %s not synced: %s", target.handle, exc)
                report.failed.append(f"{target.folder_name}: {exc}")
    finally:
        redis.delete(_LOCK_KEY)
    logger.info(
        "drive: %s synced -- %d created, %d found, %d renamed, %d day folders,"
        " %d failed",
        day,
        len(report.created),
        len(report.adopted),
        len(report.renamed),
        report.days,
        len(report.failed),
    )
    return report


def _not_shared(bot: str | None) -> str:
    return (
        "The bot cannot open the Drive folder: share it with"
        f" {bot or 'the service account'} as Editor."
    )


def _list_day(card: DriveCard, day: datetime.date) -> None:
    """Fills the card from Drive; a folder gone since is forgotten, so the
    next sweep makes it again."""
    if card.day_folder_id is None:
        return
    try:
        card.files = connect().children(card.day_folder_id)
    except FolderGoneError:
        store.forget_day_folder(card.target.login_id, day)
        card.day_folder_id = None
        card.error = "Today's folder was deleted: Sync makes it again"
    except DriveError as exc:
        logger.warning("drive: listing %s failed: %s", card.target.handle, exc)
        card.error = str(exc)


def _other_folders(known: set[str]) -> list[DriveFile]:
    root = settings.gdrive.root_folder_id
    folders = connect().children(root, folders_only=True)
    return sorted(
        (f for f in folders if f.id not in known), key=lambda f: f.name.lower()
    )


def board(day: datetime.date | None = None) -> DriveBoard:
    """/drive: a card per login with the day's files, and the root's
    folders no login owns."""
    day = day or today()
    s = settings.gdrive
    bot = service_account_email()
    result = DriveBoard(day=day, bot_email=bot, root_folder_id=s.root_folder_id)
    if not s.configured:
        result.problem = (
            "Google Drive is not set up: OFM_GDRIVE_SERVICE_ACCOUNT and"
            " OFM_GDRIVE_ROOT_FOLDER in .env."
        )
        return result
    folders = store.account_folders()
    days = store.day_folders(day)
    result.cards = [
        DriveCard(
            target=t,
            account_folder_id=f.folder_id if (f := folders.get(t.login_id)) else None,
            day_folder_id=days.get(t.login_id),
        )
        for t in store.targets()
    ]
    known = {f.folder_id for f in folders.values()}
    with ThreadPoolExecutor(max_workers=s.list_workers) as pool:
        others = pool.submit(_other_folders, known)
        for card in result.cards:
            pool.submit(_list_day, card, day)
        try:
            result.other_folders = others.result()
        except FolderGoneError:
            result.problem = _not_shared(bot)
        except DriveError as exc:
            result.problem = str(exc)
    return result


def thumbnail(file_id: str) -> tuple[bytes, str] | None:
    return connect().thumbnail(file_id, settings.gdrive.thumb_px)


async def drive_loop() -> None:
    while True:
        if settings.gdrive.configured:
            try:
                await asyncio.to_thread(sync)
            except DriveError as exc:
                # Not shared yet, key revoked: the page says so too.
                logger.warning("drive sweep: %s", exc)
            except Exception:
                # A broken sweep must never take the loop down -- log and move on.
                logger.exception("drive sweep failed")
        await asyncio.sleep(settings.gdrive.check_interval_s)
