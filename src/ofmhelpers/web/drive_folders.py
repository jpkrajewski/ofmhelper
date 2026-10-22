"""
ofmhelpers/web/drive_folders.py

/drive's folders in the shared Google Drive folder (`settings.gdrive`):
every Instagram login on an active phone gets "<handle> - <owner>" in the
root, which the VA uploads into, and a POSTED folder
(`settings.gdrive.posted_folder_name`) inside it, which people -- never
this code -- move a video into once it is posted.

The sweep (`sync`: `drive_loop` every `check_interval_s`, and the page's
Sync button) does two things under one Postgres advisory lock
(`store.sync_lock`; a second sweep at the same time does nothing):

1. makes what is missing: the database says which logins lack a folder, a
   POSTED folder, or carry an old name, and Drive is called only for those.
   Folders are found by name before one is made (a folder made by hand is
   reused, "Posted" too), then kept by id: a new owner renames the folder,
   a folder renamed by hand is renamed back.
2. tallies (`_tally`): lists every account folder and its POSTED folder, in
   parallel, records each file seen in POSTED for the first time (Drive
   keeps no time a file was moved, so "posted today" comes from this), and
   heals what it finds broken -- an account folder gone, a POSTED folder
   trashed, renamed or dragged elsewhere -- in the same pass.

Whatever state Drive and the database are in, the next sweep converges on
"each login: one account folder, one POSTED inside it". Nothing in Drive is
ever moved or deleted; other folders inside an account folder (the old day
folders) are ignored. Outside production the live folder
(`settings.gdrive.production_root_folder_id`) is refused (`root_folder`).

`board()` is what /drive shows: each account folder listed live from Drive,
in parallel (`list_workers`), with the recorded posted counts.
"""

from __future__ import annotations

import asyncio
import datetime
import time
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from ofmhelpers.config import settings
from ofmhelpers.gdrive import (
    DriveApi,
    DriveError,
    DriveFile,
    FolderGoneError,
    connect,
    service_account_email,
)
from ofmhelpers.log import get_logger
from ofmhelpers.web.schemas import (
    AccountScan,
    DriveBoard,
    DriveCard,
    DriveTarget,
    StoredFolder,
    SyncReport,
)
from ofmhelpers.web.stores import drive_folders as store

logger = get_logger(__name__)

_WEEK_DAYS = 7


class ProductionFolderError(DriveError):
    """The live Drive folder, configured outside production."""


def root_folder() -> str:
    """The configured root folder. Raises `ProductionFolderError` when it is
    the live one and this is not production: dev, CI and tests must never
    write into the folder the VA works in."""
    s = settings.gdrive
    live = s.production_root_folder_id
    if live and s.root_folder_id == live and not settings.infra.production:
        msg = (
            "Refusing the production Drive folder outside production"
            " (OFM_ENV is not 'production'): set OFM_GDRIVE_ROOT_FOLDER to a"
            " sandbox folder."
        )
        raise ProductionFolderError(msg)
    return s.root_folder_id


def now() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(settings.gdrive.timezone))


def _period_starts(at: datetime.datetime) -> list[float]:
    """[start of today, start of the last 7 days] in the Drive timezone, as
    timestamps: what "posted today / this week" count from. Each midnight
    is built in the zone itself, so a DST change between them is right."""
    first = at.date() - datetime.timedelta(days=_WEEK_DAYS - 1)
    return [
        datetime.datetime.combine(day, datetime.time(), at.tzinfo).timestamp()
        for day in (at.date(), first)
    ]


def _is_posted(name: str) -> bool:
    """A folder name meaning POSTED: "Posted", "posted " made by hand too."""
    wanted = settings.gdrive.posted_folder_name.strip().casefold()
    return name.strip().casefold() == wanted


def _missing(
    targets: list[DriveTarget], folders: dict[int, StoredFolder]
) -> list[DriveTarget]:
    """The logins with no folder, a folder under an old name, or no POSTED
    folder."""
    return [
        t
        for t in targets
        if (f := folders.get(t.login_id)) is None
        or f.name != t.folder_name
        or f.posted_folder_id is None
    ]


def _account_folder(
    drive: DriveApi,
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


def _find_posted(drive: DriveApi, account_id: str) -> DriveFile | None:
    """The oldest POSTED-named folder in the account folder (Drive lists
    newest first)."""
    named = [
        f for f in drive.children(account_id, folders_only=True) if _is_posted(f.name)
    ]
    return named[-1] if named else None


def _posted_folder(
    drive: DriveApi,
    target: DriveTarget,
    account_id: str,
    stored_id: str | None,
    report: SyncReport,
) -> str:
    """The account folder's POSTED folder: the stored one while it is still
    inside (renamed back if renamed by hand); else a POSTED-named one in it;
    else a new one."""
    name = settings.gdrive.posted_folder_name
    label = f"{target.folder_name}/{name}"
    if stored_id is not None:
        try:
            current = drive.folder(stored_id)
        except FolderGoneError:
            current = None
        if current is not None and account_id in current.parents:
            if current.name != name:
                drive.rename(current.id, name)
                report.renamed.append(label)
            return current.id
        logger.warning(
            "drive: %s of %s is gone or moved out, finding or making it again",
            name,
            target.handle,
        )
        store.forget_posted_folder(target.login_id)
    found = _find_posted(drive, account_id)
    if found is None:
        made = drive.create_folder(account_id, name)
        folder_id = made.id
    else:
        folder_id = found.id
        if found.name != name:
            drive.rename(found.id, name)
            report.renamed.append(label)
    store.save_posted_folder(target.login_id, folder_id)
    report.posted_folders.append(label)
    return folder_id


def _sync_one(
    drive: DriveApi,
    root: str,
    target: DriveTarget,
    stored: StoredFolder | None,
    report: SyncReport,
) -> None:
    account_id, kept = _account_folder(drive, root, target, stored, report)
    posted_id = stored.posted_folder_id if kept and stored else None
    _posted_folder(drive, target, account_id, posted_id, report)


def _files(items: list[DriveFile]) -> list[DriveFile]:
    return [i for i in items if i.kind != "folder"]


def _scan(drive: DriveApi, account_id: str, posted_id: str | None) -> AccountScan:
    """The account folder as it is now. Raises `FolderGoneError` when it is
    gone (a trashed folder still lists, so it is asked first)."""
    current = drive.folder(account_id)
    items = drive.children(account_id)
    folders = [i for i in items if i.kind == "folder"]
    posted_dirs = [f for f in folders if f.id == posted_id or _is_posted(f.name)]
    scan = AccountScan(
        name=current.name,
        waiting=_files(items),
        posted_folders=posted_dirs,
        extra_posted_folders=[d for d in posted_dirs if d.id != posted_id],
    )
    # Other folders in it (the old day folders, hand-made ones) are neither
    # listed nor touched: VAs clear them out themselves.
    for d in posted_dirs:
        scan.posted += _files(drive.children(d.id))
    return scan


def _kept_posted_ok(scan: AccountScan, stored: StoredFolder) -> bool:
    """Whether the stored POSTED folder is inside the account folder under
    its name. Not when it is gone, trashed, renamed or dragged out."""
    name = settings.gdrive.posted_folder_name
    return any(
        d.id == stored.posted_folder_id and d.name == name for d in scan.posted_folders
    )


def _tally_one(
    root: str, target: DriveTarget, stored: StoredFolder, seen_at: float
) -> SyncReport:
    """Lists one account, heals it, records what is newly posted. Its own
    report (merged by `_tally`): the accounts run in parallel threads, each
    with its own Drive connection."""
    report = SyncReport()
    drive = connect()
    try:
        scan = _scan(drive, stored.folder_id, stored.posted_folder_id)
    except FolderGoneError:
        logger.warning("drive: folder of %s is gone, making it again", target.handle)
        store.forget_account_folder(target.login_id)
        _sync_one(drive, root, target, None, report)
        return report
    if scan.name != target.folder_name:
        drive.rename(stored.folder_id, target.folder_name)
        report.renamed.append(target.folder_name)
    if not _kept_posted_ok(scan, stored):
        _posted_folder(drive, target, stored.folder_id, stored.posted_folder_id, report)
    report.newly_posted = store.record_posted(target.login_id, scan.posted, seen_at)
    return report


def _merge(into: SyncReport, part: SyncReport) -> None:
    into.created += part.created
    into.adopted += part.adopted
    into.renamed += part.renamed
    into.posted_folders += part.posted_folders
    into.newly_posted += part.newly_posted
    into.failed += part.failed


def _tally(root: str, report: SyncReport) -> None:
    """Every linked account, in parallel; one failing is reported."""
    folders = store.account_folders()
    linked = [(t, f) for t in store.targets() if (f := folders.get(t.login_id))]
    seen_at = time.time()
    with ThreadPoolExecutor(max_workers=settings.gdrive.list_workers) as pool:
        futures = {pool.submit(_tally_one, root, t, f, seen_at): t for t, f in linked}
        for future, target in futures.items():
            try:
                _merge(report, future.result())
            except DriveError as exc:
                logger.warning("drive: %s not tallied: %s", target.handle, exc)
                report.failed.append(f"{target.folder_name}: {exc}")


def sync() -> SyncReport:
    """Makes what is missing, then tallies and heals every account. Raises
    `DriveError` when Drive is not set up or the root is not shared; one
    login failing is reported, the others still go."""
    report = SyncReport()
    root = root_folder()
    with store.sync_lock() as held:
        if not held:
            report.busy = True
            return report
        # Read inside the lock: a sweep that just finished is seen.
        targets = store.targets()
        if not targets:
            return report
        folders = store.account_folders()
        todo = _missing(targets, folders)
        drive = connect()
        drive.folder(root)
        for target in todo:
            try:
                _sync_one(drive, root, target, folders.get(target.login_id), report)
            except DriveError as exc:
                logger.warning("drive: %s not synced: %s", target.handle, exc)
                report.failed.append(f"{target.folder_name}: {exc}")
        _tally(root, report)
    logger.info(
        "drive: synced -- %d created, %d found, %d renamed, %d POSTED folders,"
        " %d newly posted, %d failed",
        len(report.created),
        len(report.adopted),
        len(report.renamed),
        len(report.posted_folders),
        report.newly_posted,
        len(report.failed),
    )
    return report


def _not_shared(bot: str | None) -> str:
    return (
        "The bot cannot open the Drive folder: share it with"
        f" {bot or 'the service account'} as Editor."
    )


def _list_account(card: DriveCard, seen_at: float) -> None:
    """Fills the card from Drive and records what is newly posted; a folder
    gone since is forgotten, so the next sweep makes it again."""
    if card.account_folder_id is None:
        return
    try:
        drive = connect(interactive=True)
        card.scan = _scan(drive, card.account_folder_id, card.posted_folder_id)
    except FolderGoneError:
        store.forget_account_folder(card.target.login_id)
        card.account_folder_id = card.posted_folder_id = None
        card.error = "The account's folder was deleted: Sync makes it again"
        return
    except DriveError as exc:
        logger.warning("drive: listing %s failed: %s", card.target.handle, exc)
        card.error = str(exc)
        return
    store.record_posted(card.target.login_id, card.scan.posted, seen_at)


def _other_folders(root: str, known: set[str]) -> list[DriveFile]:
    """The root folders no login owns. Raises `FolderGoneError` when the
    root itself is gone or not shared (its listing would only be empty)."""
    drive = connect(interactive=True)
    drive.folder(root)
    folders = drive.children(root, folders_only=True)
    return sorted(
        (f for f in folders if f.id not in known), key=lambda f: f.name.lower()
    )


def board(at: datetime.datetime | None = None) -> DriveBoard:
    """/drive: a card per login with what waits and what was posted, and
    the root's folders no login owns."""
    at = at or now()
    s = settings.gdrive
    bot = service_account_email()
    result = DriveBoard(
        day=at.date(),
        bot_email=bot,
        root_folder_id=s.root_folder_id,
        posted_folder_name=s.posted_folder_name,
    )
    if not s.configured:
        result.problem = (
            f"Google Drive is not set up: {' and '.join(s.missing)}"
            f" {'is' if len(s.missing) == 1 else 'are'} empty in .env."
        )
        return result
    try:
        root = root_folder()
    except ProductionFolderError as exc:
        result.problem = str(exc)
        return result
    folders = store.account_folders()
    result.cards = [
        DriveCard(
            target=t,
            account_folder_id=f.folder_id if (f := folders.get(t.login_id)) else None,
            posted_folder_id=f.posted_folder_id if f else None,
        )
        for t in store.targets()
    ]
    known = {f.folder_id for f in folders.values()}
    seen_at = at.timestamp()
    with ThreadPoolExecutor(max_workers=s.list_workers) as pool:
        others = pool.submit(_other_folders, root, known)
        listed = {
            pool.submit(_list_account, card, seen_at): card for card in result.cards
        }
        try:
            result.other_folders = others.result()
        except FolderGoneError:
            result.problem = _not_shared(bot)
        except DriveError as exc:
            result.problem = str(exc)
        for future, card in listed.items():
            try:
                future.result()
            except Exception:
                # Never "Nothing waiting" for a folder never read.
                logger.exception("drive: card of %s failed", card.target.handle)
                card.error = "Could not read this folder from Drive"
    counts = store.posted_counts(_period_starts(at))
    for card in result.cards:
        card.posted_today, card.posted_week = counts.get(card.target.login_id, [0, 0])
    return result


def thumbnail(file_id: str) -> tuple[bytes, str] | None:
    """The preview of a file in one of the app's folders; None for any
    other id (not ours, gone, no preview), so the proxy never serves the
    rest of what the account can see."""
    root_folder()
    drive = connect(interactive=True)
    try:
        item = drive.file(file_id)
    except FolderGoneError:
        return None
    if not set(item.parents) & store.folder_ids():
        logger.warning("drive: preview of %s refused: not in a /drive folder", file_id)
        return None
    return drive.thumbnail(item, settings.gdrive.thumb_px)


async def drive_loop() -> None:
    try:
        root_folder()
    except ProductionFolderError as exc:
        logger.error("drive sweep not started: %s", exc)  # noqa: TRY400
        return
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
