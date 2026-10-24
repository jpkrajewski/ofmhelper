"""
ofmhelpers/web/drive_folders.py

/drive: every Instagram login on an active phone gets "<handle> - <owner>"
-- uploaded into from the phone, read here -- with a POSTED folder inside
(`settings.gdrive.posted_folder_name`) that people, never this code, move a
video into once it is posted.

Everything happens in the phone owner's OWN Google Drive, through their
own Google account (`gdrive.connect_as`, the refresh token their Google
sign-in left, `stores.users.refresh_token`): one way in for reading,
making, renaming and uploading. Each owner has one root folder there,
`root_name()` -- "ALL ACCOUNTS" in production, "DEV - ALL ACCOUNTS"
anywhere else, so a dev copy never touches the live folders -- found by
name (any case: a hand-made "all accounts" is adopted with everything in
it) or made, and kept by id (`users.drive_root_id`). An owner who never
connected Google Drive is listed in `SyncReport.not_connected` and their
cards say so; nothing is made for them -- except the agency admin (the
password account), who is read through the Drive bot (the service account,
`settings.gdrive.service_account`) in the configured root
(`OFM_GDRIVE_ROOT_FOLDER`, prod's "ALL ACCOUNTS", shared with the bot)
until they sign in with Google: /drive keeps showing the agency's folders
however the admin signed in. Only that folder is used then -- none is
searched for or made in the bot's own Drive -- and outside production only
when it is named `root_name()`. Uploads still need a Google sign-in (the bot
has no storage).

The sweep (`sync`: `drive_loop` every `check_interval_s`, and the page's
Sync button), under one Postgres advisory lock (`store.sync_lock`; a
second sweep at the same time does nothing), per owner:

1. makes what is missing: the database says which logins lack a folder, a
   POSTED folder, or carry an old name, and Drive is called only for those.
   Folders are found by name before one is made, then kept by id: a new
   owner name renames the folder, a folder renamed by hand is renamed back.
2. tallies (`_tally`): lists every account folder and its POSTED folder, in
   parallel, records each file seen in POSTED for the first time (Drive
   keeps no time a file was moved, so "posted today" comes from this), and
   heals what it finds broken -- an account folder gone, a POSTED folder
   trashed, renamed or dragged elsewhere -- in the same pass.

Nothing in Drive is ever moved or deleted; other folders inside an
account folder (the old day folders) are ignored.

`board(owner=..., live=False)` opens /drive at once from each folder's last
read (saved by every read: sweep, Sync, live page); `/drive/live` then
brings the fresh one: per owner, their root listed once, every account
folder in it in one batched listing and their POSTED folders in another
(`children_of`). Previews come through the folder owner's account too and
are kept on disk (`thumbnail`).
"""

from __future__ import annotations

import asyncio
import datetime
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from ofmhelpers.config import settings
from ofmhelpers.gdrive import (
    DriveApi,
    DriveError,
    DriveFile,
    DriveFolder,
    FolderGoneError,
    connect,
    connect_as,
    read_live_mode,
    service_account_email,
)
from ofmhelpers.log import get_logger
from ofmhelpers.signin import SignInError
from ofmhelpers.web.previews import previews_dir, write_atomic
from ofmhelpers.web.schemas import (
    AccountScan,
    DriveBoard,
    DriveCard,
    DriveTarget,
    StoredFolder,
    SyncReport,
)
from ofmhelpers.web.stores import drive_folders as store
from ofmhelpers.web.stores import drive_uploads as upload_store
from ofmhelpers.web.stores import users

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

_WEEK_DAYS = 7
# The root folder in each owner's Drive.
_ROOT_NAME = "ALL ACCOUNTS"
_DEV_PREFIX = "DEV - "


def root_name() -> str:
    """ "ALL ACCOUNTS" in production; "DEV - ALL ACCOUNTS" anywhere else, so
    dev and tests never adopt or write into the live folders, even in the
    same Google account. In live-read mode dev reads the live "ALL ACCOUNTS"
    (every write blocked, `gdrive.ReadOnlyDrive`)."""
    live = settings.infra.production or read_live_mode()
    return _ROOT_NAME if live else f"{_DEV_PREFIX}{_ROOT_NAME}"


def _bot_root(user_id: int) -> str | None:
    """The configured root the Drive bot reads for `user_id` instead of
    their own Drive: only the agency admin's, only without a Google token,
    only when the bot and the root are both configured."""
    s = settings.gdrive
    if not (s.service_account and s.root_folder_id):
        return None
    if users.refresh_token(user_id):
        return None
    if service_account_email() is None:
        logger.warning("drive: OFM_GDRIVE_SERVICE_ACCOUNT is not a usable key")
        return None
    agency = users.get_user(user_id)
    return s.root_folder_id if agency and agency.is_password_account else None


def _person_drive(user_id: int, *, interactive: bool = False) -> DriveApi | None:
    """The owner's own Drive; for the agency admin without Google, the Drive
    bot in the configured root (`_bot_root`, kept as their root); None when
    neither."""
    token = users.refresh_token(user_id)
    if token:
        return connect_as(token, interactive=interactive)
    root = _bot_root(user_id)
    if root is None:
        return None
    user = users.get_user(user_id)
    if user is not None and user.drive_root_id != root:
        users.set_drive_root(user_id, root)
    return connect(interactive=interactive)


def _owner_label(user_id: int) -> str:
    user = users.get_user(user_id)
    return user.email if user else f"user {user_id}"


def _is_root(folder: DriveFolder) -> bool:
    """Whether a stored root may be used. Outside production only a folder
    named `root_name()`: a dev copy of the prod database carries the live
    "ALL ACCOUNTS" id, which dev must never write into -- except in live-read
    mode, where it is read (never written, `gdrive.ReadOnlyDrive`)."""
    if settings.infra.production or read_live_mode():
        return True
    return folder.name.strip().casefold() == root_name().casefold()


def usable_root(drive: DriveApi, user_id: int) -> str | None:
    """The owner's stored root while it is still there and (outside
    production) still named `root_name()`; else None. Only asks: nothing is
    made or forgotten (the upload worker checks with it before writing).
    Raises `DriveError`."""
    user = users.get_user(user_id)
    stored = user.drive_root_id if user else None
    if not stored:
        return None
    try:
        current = drive.folder(stored)
    except FolderGoneError:
        logger.warning("drive: root of %s is gone", user_id)
        return None
    if not _is_root(current):
        logger.warning(
            "drive: stored root of %s is %r, not %s: not used",
            user_id,
            current.name,
            root_name(),
        )
        return None
    return stored


def ensure_root(drive: DriveApi, user_id: int) -> str:
    """The owner's root folder: `usable_root`; else the oldest folder in
    their My Drive whose name is `root_name()` in any case (one made by
    hand is adopted with what is in it); else a new one. A stored folder of
    another name is left as it is. Raises `DriveError`."""
    stored = usable_root(drive, user_id)
    if stored:
        return stored
    if _bot_root(user_id) is not None:
        # The bot's own Drive is no place for the agency's folders.
        msg = (
            "The Drive bot cannot use OFM_GDRIVE_ROOT_FOLDER: share it with"
            f" {service_account_email()}"
            + ("" if settings.infra.production else f" and name it {root_name()}")
        )
        raise DriveError(msg)
    wanted = root_name().casefold()
    named = [
        f
        for f in drive.children("root", folders_only=True)
        if f.name.strip().casefold() == wanted
    ]
    # Drive lists newest first: the oldest is the one people have been using.
    found = named[-1].id if named else drive.create_folder("root", root_name()).id
    users.set_drive_root(user_id, found)
    logger.info(
        "drive: root of %s is %s (%s)", _owner_label(user_id), found, root_name()
    )
    return found


def _by_owner(targets: list[DriveTarget]) -> dict[int, list[DriveTarget]]:
    grouped: dict[int, list[DriveTarget]] = defaultdict(list)
    for target in targets:
        grouped[target.device.user_id].append(target)
    return dict(grouped)


def now() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(settings.gdrive.timezone))


def _period_starts(at: datetime.datetime) -> list[float]:
    """[start of today, start of the last 7 days] in the Drive timezone, as
    timestamps: what "Posted today / Last 7 days" count from. By the local
    calendar day, not the posting window -- the team posts in the evening, so
    a window starting mid-evening would count those posts as yesterday's."""
    local = at.astimezone(ZoneInfo(settings.gdrive.timezone))
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - datetime.timedelta(days=_WEEK_DAYS - 1)
    return [today.timestamp(), first.timestamp()]


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


def _deleted_by_hand(target: DriveTarget) -> None:
    """Its folder was deleted in Drive: the account goes everywhere
    (web/account_removal.py), the folder is not made again. Imported here:
    the removal reads phones and trashes through this module."""
    from ofmhelpers.web.account_removal import folder_deleted

    logger.warning(
        "drive: folder of %s deleted by hand: removing the account", target.handle
    )
    store.forget_account_folder(target.login_id)
    folder_deleted(target.device.user_id, target.login_id)


def trash_folders(user_id: int, login_ids: list[int]) -> None:
    """These logins' account folders into the owner's Drive trash
    (restorable there), forgotten. A Drive failure is logged, never raised:
    the folder then stays, listed on /drive under folders no login owns."""
    stored = store.account_folders()
    folders = [stored[i] for i in login_ids if i in stored]
    if not folders:
        return
    try:
        drive = _person_drive(user_id)
    except (DriveError, SignInError):
        logger.warning("drive: cannot trash folders of %s", user_id, exc_info=True)
        return
    for folder in folders:
        try:
            if drive is not None:
                drive.trash_file(folder.folder_id)
        except FolderGoneError:
            pass
        except DriveError:
            logger.warning("drive: trashing %s failed", folder.folder_id, exc_info=True)
            continue
        store.forget_account_folder(folder.login_id)


class _RemovedByHandError(Exception):
    """The stored account folder was deleted in Drive (`_deleted_by_hand`)."""


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
        except FolderGoneError as exc:
            _deleted_by_hand(target)
            raise _RemovedByHandError from exc
        else:
            if current.name != target.folder_name:
                drive.rename(current.id, target.folder_name)
                report.renamed.append(target.folder_name)
            if stored.name != target.folder_name:
                store.save_account_folder(
                    target.login_id, current.id, target.folder_name, root
                )
            return current.id, True
    found = drive.find_folder(root, target.folder_name)
    if found is not None:
        report.adopted.append(target.folder_name)
    else:
        found = drive.create_folder(root, target.folder_name)
        report.created.append(target.folder_name)
    store.save_account_folder(target.login_id, found.id, target.folder_name, root)
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
    try:
        account_id, kept = _account_folder(drive, root, target, stored, report)
    except _RemovedByHandError:
        return
    posted_id = stored.posted_folder_id if kept and stored else None
    _posted_folder(drive, target, account_id, posted_id, report)


def _files(items: list[DriveFile]) -> list[DriveFile]:
    return [i for i in items if i.kind != "folder"]


def _posted_dirs(items: list[DriveFile], posted_id: str | None) -> list[DriveFile]:
    """The POSTED folders among an account folder's items: the kept one
    (under whatever name it has now) and any other POSTED-named one."""
    return [
        i
        for i in items
        if i.kind == "folder" and (i.id == posted_id or _is_posted(i.name))
    ]


def _build_scan(
    name: str,
    items: list[DriveFile],
    posted_id: str | None,
    posted_items: dict[str, list[DriveFile]],
) -> AccountScan:
    """An account folder from its listing and its POSTED folders' ones.
    Other folders in it (the old day folders, hand-made ones) are neither
    listed nor touched: VAs clear them out themselves."""
    posted_dirs = _posted_dirs(items, posted_id)
    return AccountScan(
        name=name,
        waiting=_files(items),
        posted_folders=posted_dirs,
        extra_posted_folders=[d for d in posted_dirs if d.id != posted_id],
        posted=[f for d in posted_dirs for f in _files(posted_items.get(d.id, []))],
    )


def _scan(drive: DriveApi, account_id: str, posted_id: str | None) -> AccountScan:
    """The account folder as it is now. Raises `FolderGoneError` when it is
    gone (a trashed folder still lists, so it is asked first)."""
    current = drive.folder(account_id)
    items = drive.children(account_id)
    posted_items: dict[str, list[DriveFile]] = {}
    for d in _posted_dirs(items, posted_id):
        try:
            posted_items[d.id] = drive.children(d.id)
        except FolderGoneError:
            # Only that POSTED folder went since the listing, not the
            # account: never taken as the account deleted by hand.
            logger.info("drive: POSTED folder %s gone mid-scan", d.id)
    return _build_scan(current.name, items, posted_id, posted_items)


def _kept_posted_ok(scan: AccountScan, stored: StoredFolder) -> bool:
    """Whether the stored POSTED folder is inside the account folder under
    its name. Not when it is gone, trashed, renamed or dragged out."""
    name = settings.gdrive.posted_folder_name
    return any(
        d.id == stored.posted_folder_id and d.name == name for d in scan.posted_folders
    )


def _tally_one(
    user_id: int, target: DriveTarget, stored: StoredFolder, seen_at: float
) -> SyncReport:
    """Lists one account, heals it, records what is newly posted. Its own
    report (merged by `_tally`): the accounts run in parallel threads, each
    with its own Drive connection."""
    report = SyncReport()
    drive = _person_drive(user_id)
    if drive is None:
        return report
    try:
        scan = _scan(drive, stored.folder_id, stored.posted_folder_id)
    except FolderGoneError:
        _deleted_by_hand(target)
        return report
    if scan.name != target.folder_name:
        drive.rename(stored.folder_id, target.folder_name)
        report.renamed.append(target.folder_name)
    if not _kept_posted_ok(scan, stored):
        _posted_folder(drive, target, stored.folder_id, stored.posted_folder_id, report)
    report.newly_posted = store.record_posted(
        target.login_id, stored.folder_id, scan.posted, seen_at
    )
    store.save_scans({target.login_id: scan}, seen_at)
    # A video deleted (or dragged away) in Drive stops showing "In Drive".
    present = {f.id for f in (*scan.waiting, *scan.posted)}
    upload_store.gone_from(target.login_id, present, seen_at)
    return report


def _merge(into: SyncReport, part: SyncReport) -> None:
    into.created += part.created
    into.adopted += part.adopted
    into.renamed += part.renamed
    into.posted_folders += part.posted_folders
    into.newly_posted += part.newly_posted
    into.failed += part.failed


def _tally(user_id: int, targets: list[DriveTarget], report: SyncReport) -> None:
    """Every linked account of the owner, in parallel; one failing is
    reported."""
    folders = store.account_folders()
    linked = [(t, f) for t in targets if (f := folders.get(t.login_id))]
    seen_at = time.time()
    with ThreadPoolExecutor(max_workers=settings.gdrive.list_workers) as pool:
        futures = {
            pool.submit(_tally_one, user_id, t, f, seen_at): t for t, f in linked
        }
        for future, target in futures.items():
            try:
                _merge(report, future.result())
            except (DriveError, SignInError) as exc:
                logger.warning("drive: %s not tallied: %s", target.handle, exc)
                report.failed.append(f"{target.folder_name}: {exc}")


def _sync_owner(user_id: int, targets: list[DriveTarget], report: SyncReport) -> None:
    drive = _person_drive(user_id)
    if drive is None:
        report.not_connected.append(_owner_label(user_id))
        return
    try:
        root = ensure_root(drive, user_id)
    except (DriveError, SignInError) as exc:
        logger.warning("drive: root of %s failed: %s", user_id, exc)
        report.failed.append(f"{_owner_label(user_id)}: {exc}")
        return
    folders = store.account_folders()
    for target in _missing(targets, folders):
        try:
            _sync_one(drive, root, target, folders.get(target.login_id), report)
        except (DriveError, SignInError) as exc:
            logger.warning("drive: %s not synced: %s", target.handle, exc)
            report.failed.append(f"{target.folder_name}: {exc}")
    _tally(user_id, targets, report)


def sync(owner: int | None = None) -> SyncReport:
    """Per phone owner (only `owner`: a person's Sync, admins included;
    everyone for None: the sweep): their root, what is missing in it, then
    tallies and heals every account. One owner or login failing is reported; the
    others still go. The report names only the owners swept."""
    report = SyncReport()
    if read_live_mode():
        # Live-read mode only reads the live folders (the page's /drive/live);
        # the sweep makes and renames folders, so it never runs here.
        logger.info("drive: live-read mode, sweep skipped (no writes to live)")
        return report
    with store.sync_lock() as held:
        if not held:
            report.busy = True
            return report
        # Read inside the lock: a sweep that just finished is seen.
        for user_id, targets in _by_owner(store.targets(owner)).items():
            _sync_owner(user_id, targets, report)
    logger.info(
        "drive: synced -- %d created, %d found, %d renamed, %d POSTED folders,"
        " %d newly posted, %d not connected, %d failed",
        len(report.created),
        len(report.adopted),
        len(report.renamed),
        len(report.posted_folders),
        report.newly_posted,
        len(report.not_connected),
        len(report.failed),
    )
    return report


def _list_account(user_id: int, card: DriveCard, seen_at: float) -> None:
    """Fills the card from Drive and records what is newly posted; a folder
    deleted by hand since removes the account everywhere."""
    if card.account_folder_id is None:
        return
    try:
        drive = _person_drive(user_id, interactive=True)
        if drive is None:
            return
        folder_id = card.account_folder_id
        card.scan = _scan(drive, folder_id, card.posted_folder_id)
    except FolderGoneError:
        _deleted_by_hand(card.target)
        card.account_folder_id = card.posted_folder_id = None
        card.error = "The account's folder was deleted: the account was removed"
        return
    except (DriveError, SignInError) as exc:
        logger.warning("drive: listing %s failed: %s", card.target.handle, exc)
        card.error = str(exc)
        return
    store.record_posted(card.target.login_id, folder_id, card.scan.posted, seen_at)
    store.save_scans({card.target.login_id: card.scan}, seen_at)


def _root_folders(user_id: int, drive: DriveApi, root: str) -> list[DriveFile]:
    """The root's folders. Raises `FolderGoneError` when the root itself is
    gone, or (outside production) is not named `root_name()` (a trashed one
    would still list, so it is asked too, at the same time: each Drive
    round trip is a few hundred ms)."""

    def check() -> None:
        other = _person_drive(user_id, interactive=True)
        if other is None:
            return
        current = other.folder(root)
        if not _is_root(current):
            msg = f"Root {root} is {current.name!r}, not {root_name()!r}"
            raise FolderGoneError(msg)

    with ThreadPoolExecutor(max_workers=1) as pool:
        checked = pool.submit(check)
        listed = drive.children(root, folders_only=True)
        checked.result()
    return listed


def _list_batched(
    drive: DriveApi, cards: list[DriveCard], names: dict[str, str], seen_at: float
) -> None:
    """Fills the cards whose folder the root listing just returned (live,
    not trashed), all at once: one listing for every account folder, one
    for every POSTED folder in them -- not three calls per account."""
    ids = [c.account_folder_id for c in cards if c.account_folder_id]
    inside = drive.children_of(ids)
    posted_ids = [
        d.id
        for c in cards
        for d in _posted_dirs(inside[c.account_folder_id or ""], c.posted_folder_id)
    ]
    posted_items = drive.children_of(posted_ids)
    for card in cards:
        folder_id = card.account_folder_id or ""
        card.scan = _build_scan(
            names[folder_id], inside[folder_id], card.posted_folder_id, posted_items
        )
        store.record_posted(card.target.login_id, folder_id, card.scan.posted, seen_at)
    store.save_scans({c.target.login_id: c.scan for c in cards}, seen_at)


def _list_each(user_id: int, cards: list[DriveCard], seen_at: float) -> None:
    """One account at a time (in parallel): the folders the batch could not
    take."""
    with ThreadPoolExecutor(max_workers=settings.gdrive.list_workers) as pool:
        listed = {
            pool.submit(_list_account, user_id, card, seen_at): card for card in cards
        }
        for future, card in listed.items():
            try:
                future.result()
            except Exception:
                # Never "Nothing waiting" for a folder never read.
                logger.exception("drive: card of %s failed", card.target.handle)
                card.error = "Could not read this folder from Drive"


def _list_root(
    result: DriveBoard,
    user_id: int,
    drive: DriveApi,
    root: str,
    cards: list[DriveCard],
    known: set[str] | None,
    seen_at: float,
) -> dict[str, str] | None:
    """{folder id: name} of the cards the batch filled; None when the root
    is gone (or not `root_name()`), which is forgotten. Only that check
    forgets the root; a batch failing (one account or POSTED folder deleted
    since the root listing 404s the whole query) leaves every card to the
    one-by-one listing. `known`: list the root's other folders."""
    try:
        listed = _root_folders(user_id, drive, root)
    except FolderGoneError:
        logger.warning("drive: root of %s is gone, the sweep finds it", user_id)
        users.set_drive_root(user_id, None)  # the sweep finds or makes it
        return None
    except Exception:
        logger.exception("drive: root of %s not listed, listing one by one", user_id)
        return {}
    in_root = {f.id: f.name for f in listed}
    if known is not None:
        result.other_folders = sorted(
            (f for f in listed if f.id not in known), key=lambda f: f.name.lower()
        )
    try:
        _list_batched(
            drive,
            [c for c in cards if c.account_folder_id in in_root],
            in_root,
            seen_at,
        )
    except Exception:
        logger.exception("drive: batched listing failed, listing one by one")
        return {}
    return in_root


def _list_owner(
    result: DriveBoard,
    user_id: int,
    cards: list[DriveCard],
    known: set[str],
    seen_at: float,
    *,
    others: bool,
) -> None:
    """One owner's cards, read live through their Drive: their root once,
    the folders in it in one batch, the rest one by one. `others`: also
    list the root's folders no login owns (the viewer's own root)."""
    not_connected = (
        f"{_owner_label(user_id)} has not connected Google Drive: their"
        " Account page, Connect Google Drive"
    )
    drive = _person_drive(user_id, interactive=True)
    if drive is None:
        for card in cards:
            card.error = not_connected
        return
    user = users.get_user(user_id)
    root = user.drive_root_id if user else None
    in_root = (
        _list_root(
            result, user_id, drive, root, cards, known if others else None, seen_at
        )
        if root
        else {}
    )
    if in_root is None:
        # Its folders are no longer in a root of theirs: never read them.
        for card in cards:
            card.account_folder_id = card.posted_folder_id = None
        return
    _list_each(
        user_id,
        [
            c
            for c in cards
            if c.account_folder_id is not None and c.account_folder_id not in in_root
        ],
        seen_at,
    )


def _saved_board(result: DriveBoard, folders: dict[int, StoredFolder]) -> None:
    """Fills the cards from each folder's last read: no Drive call."""
    result.live = False
    reads: list[float] = []
    for card in result.cards:
        stored = folders.get(card.target.login_id)
        if stored is None:
            continue
        if stored.scan is None or stored.scanned_at is None:
            card.unread = True
            continue
        card.scan = stored.scan
        reads.append(stored.scanned_at)
    result.as_of = min(reads) if reads else None


def board(
    at: datetime.datetime | None = None,
    owner: int | None = None,
    *,
    viewer: int | None = None,
    live: bool = True,
) -> DriveBoard:
    """/drive for `owner`'s phones (everyone's for None: tests and tools, never a
    session -- /drive passes the viewer's own id): a card
    per login with what waits and what was posted; the `viewer`'s own root
    link, and that root's folders no login owns."""
    at = at or now()
    me = users.get_user(viewer) if viewer is not None else None
    result = DriveBoard(
        day=at.date(),
        root_folder_id=(me.drive_root_id or "") if me else "",
        root_name=root_name(),
        connected=bool(me and me.drive_connected),
        posted_folder_name=settings.gdrive.posted_folder_name,
    )
    if not settings.signin.google_configured and not settings.infra.fake_externals:
        result.problem = (
            "Google Drive works through each person's Google sign-in, which is"
            " not set up: OFM_GOOGLE_CLIENT_ID and OFM_GOOGLE_CLIENT_SECRET are"
            " empty in .env."
        )
        return result
    if me is not None and not me.drive_connected and _bot_root(me.id) is None:
        result.problem = (
            f"Your phones' folders live in your own Google Drive ({root_name()})."
            " Connect it once and they are made for you."
        )
        result.connect_drive = True
    folders = store.account_folders()
    result.cards = [
        DriveCard(
            target=t,
            account_folder_id=f.folder_id if (f := folders.get(t.login_id)) else None,
            posted_folder_id=f.posted_folder_id if f else None,
        )
        for t in store.targets(owner)
    ]
    if not live:
        _saved_board(result, folders)
        _count_posted(result, at)
        return result
    known = {f.folder_id for f in folders.values()}
    seen_at = at.timestamp()
    grouped: dict[int, list[DriveCard]] = defaultdict(list)
    for card in result.cards:
        grouped[card.target.device.user_id].append(card)
    for user_id, cards in grouped.items():
        _list_owner(result, user_id, cards, known, seen_at, others=user_id == viewer)
    _count_posted(result, at)
    return result


def _count_posted(result: DriveBoard, at: datetime.datetime) -> None:
    counts = store.posted_counts(_period_starts(at))
    for card in result.cards:
        card.posted_today, card.posted_week = counts.get(card.target.login_id, [0, 0])


def _thumb_cache(file_id: str, px: int) -> Path:
    return previews_dir() / "drive" / f"{file_id}_{px}"


def _cached_thumb(file_id: str, px: int) -> tuple[bytes, str] | None:
    """A preview fetched before: (bytes, type), kept as `<id>_<px>.<subtype>`."""
    base = _thumb_cache(file_id, px)
    if not base.parent.is_dir():
        return None
    for path in base.parent.glob(f"{base.name}.*"):
        return path.read_bytes(), f"image/{path.suffix.lstrip('.')}"
    return None


def _keep_thumb(file_id: str, px: int, content: bytes, media_type: str) -> None:
    subtype = media_type.split("/", 1)[-1].split(";", 1)[0].strip() or "jpeg"
    dest = _thumb_cache(file_id, px).with_suffix(f".{subtype}")

    def write(tmp: Path) -> None:
        tmp.write_bytes(content)

    try:
        write_atomic(dest, write)
    except OSError:
        logger.warning("drive: could not cache preview of %s", file_id, exc_info=True)


def thumbnail(
    login_id: int, file_id: str, owner: int | None = None
) -> tuple[bytes, str] | None:
    """The preview of a file in `login_id`'s account or POSTED folder, read
    through the folder owner's Drive; None for anything else (another
    folder, gone, no preview, a login `owner` may not see), so the proxy
    never serves the rest of what the account can see. Whether it is in
    the folder is asked every time (it may have been moved since); the
    image itself is kept on disk once fetched."""
    target = next((t for t in store.targets(owner) if t.login_id == login_id), None)
    folder = store.account_folders().get(login_id) if target else None
    if target is None or folder is None:
        return None
    drive = _person_drive(target.device.user_id, interactive=True)
    if drive is None:
        return None
    try:
        item = drive.file(file_id)
    except FolderGoneError:
        return None
    if not set(item.parents) & {folder.folder_id, folder.posted_folder_id}:
        logger.warning("drive: preview of %s refused: not in that folder", file_id)
        return None
    px = settings.gdrive.thumb_px
    cached = _cached_thumb(file_id, px)
    if cached is not None:
        return cached
    found = drive.thumbnail(item, px)
    if found is not None:
        _keep_thumb(file_id, px, *found)
    return found


async def drive_loop() -> None:
    while True:
        if settings.signin.google_configured or settings.infra.fake_externals:
            try:
                await asyncio.to_thread(sync)
            except Exception:
                # A broken sweep must never take the loop down -- log and move on.
                logger.exception("drive sweep failed")
        await asyncio.sleep(settings.gdrive.check_interval_s)
