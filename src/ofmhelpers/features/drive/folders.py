import datetime
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Never
from zoneinfo import ZoneInfo
lazy from collections.abc import Iterator
lazy from pathlib import Path

from pydantic import BaseModel, SkipValidation
from redis.exceptions import LockError, RedisError

from ofmhelpers.cache import get_redis, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.drive_folders import store
from ofmhelpers.db.drive_folders.models import DriveBoard, DriveCard, DriveTarget
from ofmhelpers.db.drive_folders.schema import (
    AccountScan,
    StoredFolder,
    SyncReport,
    UploadedToday,
)
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.tracked_accounts.store import untrack
from ofmhelpers.db.users import store as users
from ofmhelpers.features.drive import va_folders
from ofmhelpers.features.previews.render import previews_dir, write_atomic
from ofmhelpers.integrations.gdrive import (
    DriveApi,
    DriveError,
    DriveFile,
    DriveFolder,
    FolderGoneError,
    ScopedDrive,
    connect,
    connect_as,
    read_live_mode,
    service_account_email,
)
from ofmhelpers.integrations.gdrive.models import (
    DriveListing,
    DriveThumb,
    FolderItems,
)
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.log import get_logger
from ofmhelpers.monitoring.telemetry import in_this_trace
lazy from ofmhelpers.config.types import AccountFolder
lazy from ofmhelpers.db.drive_folders.schema import StoredFolders

logger = get_logger(__name__)

SYNC_TASK = "drive_sync"

_ROOT_NAME = "ALL ACCOUNTS"
_DEV_PREFIX = "DEV - "


def enabled() -> bool:
    return settings.signin.google_configured or settings.infra.fake_externals


def root_name() -> str:
    live = settings.infra.production or read_live_mode()
    return _ROOT_NAME if live else f"{_DEV_PREFIX}{_ROOT_NAME}"


def _bot_root(user_id: int) -> str | None:
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


def person_drive(user_id: int, *, interactive: bool = False) -> DriveApi | None:
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
    if settings.infra.production or read_live_mode():
        return True
    return folder.name.strip().casefold() == root_name().casefold()


def usable_root(drive: DriveApi, user_id: int) -> str | None:
    user = users.get_user(user_id)
    stored = user.drive_root_id if user else None
    if not stored:
        return None
    try:
        current = drive.folder(stored)
    except FolderGoneError:
        logger.warning("drive: root of %s is gone", user_id, exc_info=True)
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


def find_root(drive: DriveApi, user_id: int) -> str | None:
    stored = usable_root(drive, user_id)
    if stored:
        return stored
    if _bot_root(user_id) is not None:
        return None
    wanted = root_name().casefold()
    named = [
        f
        for f in drive.children("root", folders_only=True)
        if f.name.strip().casefold() == wanted
    ]
    return named[-1].id if named else None


def ensure_root(drive: DriveApi, user_id: int) -> str:
    stored = usable_root(drive, user_id)
    if stored:
        return stored
    if _bot_root(user_id) is not None:
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
    found = named[-1].id if named else drive.create_folder("root", root_name()).id
    users.set_drive_root(user_id, found)
    logger.info(
        "drive: root of %s is %s (%s)", _owner_label(user_id), found, root_name()
    )
    return found


class ReviewPlace(BaseModel, frozen=True, arbitrary_types_allowed=True):
    drive: SkipValidation[DriveApi]
    folder_id: str


def review_target(refresh_token: str, user_id: int) -> ReviewPlace:
    if not read_live_mode():
        drive = connect_as(refresh_token)
        return ReviewPlace(drive=drive, folder_id=ensure_root(drive, user_id))
    raw = connect_as(refresh_token, guard=False)
    name = f"{_DEV_PREFIX}{_ROOT_NAME}"
    named = [
        f
        for f in raw.children("root", folders_only=True)
        if f.name.strip().casefold() == name.casefold()
    ]
    root = named[-1].id if named else raw.create_folder("root", name).id
    return ReviewPlace(drive=ScopedDrive(raw, root), folder_id=root)


def account_subfolder(
    place: ReviewPlace, account_folder_id: str, name: AccountFolder
) -> str:
    drive = place.drive
    if not read_live_mode():
        return drive.ensure_folder(account_folder_id, name).id
    account = drive.folder(account_folder_id)
    twin = drive.ensure_folder(place.folder_id, account.name).id
    return drive.ensure_folder(twin, name).id


@contextmanager
def _review_lock(user_id: int) -> Iterator[None]:
    wait_s = settings.gdrive.review_lock_s
    lock = get_redis().lock(
        f"lock:drive-review:{user_id}", timeout=wait_s, blocking_timeout=wait_s
    )
    try:
        held = bool(lock.acquire())
    except RedisError:
        logger.warning("drive: review lock of %s unavailable", user_id, exc_info=True)
        held = False
    else:
        if not held:
            logger.warning(
                "drive: review lock of %s not had in %s s: going ahead", user_id, wait_s
            )
    try:
        yield
    finally:
        if held:
            try:
                lock.release()
            except RedisError, LockError:
                logger.warning(
                    "drive: review lock of %s not released", user_id, exc_info=True
                )


def _folder_key(user_id: int, root: str, name: str) -> str:
    return f"drive:review-folder:{user_id}:{root}:{name}"


def _remembered(drive: DriveApi, key: str, root: str, name: str) -> str | None:
    known = get_text(key)
    if not known:
        return None
    try:
        current = drive.folder(known)
    except FolderGoneError:  # silent: deleted by hand: found again
        logger.info(
            "drive: review folder %s (%s) is gone: finding it again", name, known
        )
        return None
    if current.name != name or root not in current.parents:
        return None
    return known


def _review_folder_id(drive: DriveApi, user_id: int, root: str, name: str) -> str:
    key = _folder_key(user_id, root, name)
    known = _remembered(drive, key, root, name)
    if known:
        return known
    found = drive.find_folder(root, name)
    if found is None:
        made = drive.create_folder(root, name)
        found = drive.find_folder(root, name) or made
    set_text(key, found.id, settings.gdrive.review_folder_ttl_s)
    return found.id


def review_folder(refresh_token: str, user_id: int, name: str) -> ReviewPlace:
    with _review_lock(user_id):
        root = review_target(refresh_token, user_id)
        return ReviewPlace(
            drive=root.drive,
            folder_id=_review_folder_id(root.drive, user_id, root.folder_id, name),
        )


class _OwnerTargets(BaseModel):
    user_id: int
    targets: list[DriveTarget]


def _by_owner(targets: list[DriveTarget]) -> list[_OwnerTargets]:
    grouped: dict[int, list[DriveTarget]] = defaultdict(list)
    for target in targets:
        grouped[target.device.user_id].append(target)
    return [_OwnerTargets(user_id=u, targets=t) for u, t in grouped.items()]


def now() -> datetime.datetime:
    return datetime.datetime.now(ZoneInfo(settings.gdrive.timezone))


def _today_start(at: datetime.datetime) -> datetime.datetime:
    local = at.astimezone(ZoneInfo(settings.gdrive.timezone))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _is_posted(name: str) -> bool:
    wanted = settings.gdrive.posted_folder_name.strip().casefold()
    return name.strip().casefold() == wanted


def _missing(targets: list[DriveTarget], folders: StoredFolders) -> list[DriveTarget]:
    return [
        t
        for t in targets
        if (f := folders.get(t.login_id)) is None
        or f.name != t.folder_name
        or f.posted_folder_id is None
    ]


def review_folders() -> set[str]:
    s = settings.clone_links
    return {s.good_folder, s.trial_folder}


def _deleted_by_hand(target: DriveTarget) -> None:
    logger.warning(
        "drive: folder of %s deleted by hand: removing the account", target.handle
    )
    store.forget_account_folder(target.login_id)
    owner, login_id = target.device.user_id, target.login_id
    tracked = sorted(
        {
            login.tracked_account_id
            for phone in devices.list_devices(owner)
            for login in phone.logins
            if login.id == login_id and login.tracked_account_id
        }
    )
    devices.archive_logins([login_id], owner)
    if tracked:
        untrack(tracked, owner)
    logger.info(
        "drive folder of login %s deleted by hand: removed everywhere", login_id
    )


def trash_folders(user_id: int, login_ids: list[int]) -> None:
    stored = store.account_folders()
    folders = [f for i in login_ids if (f := stored.get(i)) is not None]
    if not folders:
        return
    try:
        drive = person_drive(user_id)
    except DriveError, SignInError:
        logger.warning("drive: cannot trash folders of %s", user_id, exc_info=True)
        return
    for folder in folders:
        try:
            if drive is not None:
                drive.trash_file(folder.folder_id)
        except FolderGoneError:  # silent: already gone
            pass
        except DriveError:
            logger.warning("drive: trashing %s failed", folder.folder_id, exc_info=True)
            continue
        store.forget_account_folder(folder.login_id)


class _RemovedByHandError(Exception): ...


class _LoginGoneError(Exception): ...


def _gone_meanwhile(target: DriveTarget) -> Never:
    logger.info("drive: %s gone meanwhile, its folder left as it is", target.handle)
    raise _LoginGoneError


class _AccountFolder(BaseModel, frozen=True):
    folder_id: str
    kept: bool


def _account_folder(
    drive: DriveApi,
    root: str,
    target: DriveTarget,
    stored: StoredFolder | None,
    report: SyncReport,
) -> _AccountFolder:
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
            if stored.name != target.folder_name and not store.save_account_folder(
                target.login_id, current.id, target.folder_name, root
            ):
                _gone_meanwhile(target)
            return _AccountFolder(folder_id=current.id, kept=True)
    found = drive.find_folder(root, target.folder_name)
    adopted = found is not None
    if found is None:
        found = drive.create_folder(root, target.folder_name)
    if not store.save_account_folder(
        target.login_id, found.id, target.folder_name, root
    ):
        _gone_meanwhile(target)
    (report.adopted if adopted else report.created).append(target.folder_name)
    return _AccountFolder(folder_id=found.id, kept=False)


def _find_posted(drive: DriveApi, account_id: str) -> DriveFile | None:
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
    name = settings.gdrive.posted_folder_name
    label = f"{target.folder_name}/{name}"
    if stored_id is not None:
        try:
            current = drive.folder(stored_id)
        except FolderGoneError:  # silent: deleted by hand: made again
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
        account = _account_folder(drive, root, target, stored, report)
    except _RemovedByHandError, _LoginGoneError:  # silent: handled where raised
        return
    posted_id = stored.posted_folder_id if account.kept and stored else None
    _posted_folder(drive, target, account.folder_id, posted_id, report)


def _files(items: list[DriveFile]) -> list[DriveFile]:
    return [i for i in items if i.kind != "folder"]


def _posted_dirs(items: list[DriveFile], posted_id: str | None) -> list[DriveFile]:
    return [
        i
        for i in items
        if i.kind == "folder" and (i.id == posted_id or _is_posted(i.name))
    ]


def _build_scan(
    name: str,
    items: list[DriveFile],
    posted_id: str | None,
    posted_items: DriveListing,
) -> AccountScan:
    posted_dirs = _posted_dirs(items, posted_id)
    return AccountScan(
        name=name,
        waiting=_files(items),
        posted_folders=posted_dirs,
        extra_posted_folders=[d for d in posted_dirs if d.id != posted_id],
        posted=[f for d in posted_dirs for f in _files(posted_items.of(d.id))],
    )


class _Scanned(BaseModel):
    scan: AccountScan
    items: list[DriveFile]


def _spoofed(drive: DriveApi, items: list[DriveFile]) -> list[DriveFile]:
    found: list[DriveFile] = []
    for d in items:
        if d.kind != "folder" or d.name != AccountFolder.SPOOFED:
            continue
        try:
            found += _files(drive.children(d.id))
        except FolderGoneError:  # silent: only that SPOOFED folder went
            logger.info("drive: SPOOFED folder %s gone mid-scan", d.id)
    return found


def _scan(drive: DriveApi, account_id: str, posted_id: str | None) -> _Scanned:
    current = drive.folder(account_id)
    items = drive.children(account_id)
    posted_items = DriveListing()
    for d in _posted_dirs(items, posted_id):
        try:
            posted_items.folders.append(
                FolderItems(folder_id=d.id, items=drive.children(d.id))
            )
        except FolderGoneError:  # silent: only that POSTED folder went
            logger.info("drive: POSTED folder %s gone mid-scan", d.id)
    return _Scanned(
        scan=_build_scan(current.name, items, posted_id, posted_items), items=items
    )


def _kept_posted_ok(scan: AccountScan, stored: StoredFolder) -> bool:
    name = settings.gdrive.posted_folder_name
    return any(
        d.id == stored.posted_folder_id and d.name == name for d in scan.posted_folders
    )


def _tally_one(
    user_id: int, target: DriveTarget, stored: StoredFolder, seen_at: float
) -> SyncReport:
    report = SyncReport()
    drive = person_drive(user_id)
    if drive is None:
        return report
    try:
        scanned = _scan(drive, stored.folder_id, stored.posted_folder_id)
    except FolderGoneError:  # silent: deleted by hand: the account is removed
        _deleted_by_hand(target)
        return report
    scan = scanned.scan
    if scan.name != target.folder_name:
        drive.rename(stored.folder_id, target.folder_name)
        report.renamed.append(target.folder_name)
    if not _kept_posted_ok(scan, stored):
        _posted_folder(drive, target, stored.folder_id, stored.posted_folder_id, report)
    newly = store.record_posted(target.login_id, stored.folder_id, scan.posted, seen_at)
    if newly is None:
        logger.info("drive: %s gone meanwhile, not tallied", target.handle)
        return report
    report.newly_posted = newly
    store.save_scans({target.login_id: scan}, seen_at)
    spoofed = _spoofed(drive, scanned.items)
    present = {f.id for f in (*scan.waiting, *scan.posted, *spoofed)}
    upload_store.follow_drive(target.login_id, present, seen_at)
    return report


def _merge(into: SyncReport, part: SyncReport) -> None:
    into.created += part.created
    into.adopted += part.adopted
    into.renamed += part.renamed
    into.posted_folders += part.posted_folders
    into.newly_posted += part.newly_posted
    into.failed += part.failed


def _tally(user_id: int, targets: list[DriveTarget], report: SyncReport) -> None:
    folders = store.account_folders()
    linked = [(t, f) for t in targets if (f := folders.get(t.login_id))]
    seen_at = time.time()
    with ThreadPoolExecutor(max_workers=settings.gdrive.list_workers) as pool:
        futures = {
            pool.submit(in_this_trace(_tally_one), user_id, t, f, seen_at): t
            for t, f in linked
        }
        for future, target in futures.items():
            try:
                _merge(report, future.result())
            except (DriveError, SignInError) as exc:
                logger.warning(
                    "drive: %s not tallied: %s", target.handle, exc, exc_info=True
                )
                report.failed.append(f"{target.folder_name}: {exc}")


def _sync_owner(user_id: int, targets: list[DriveTarget], report: SyncReport) -> None:
    drive = person_drive(user_id)
    if drive is None:
        report.not_connected.append(_owner_label(user_id))
        return
    try:
        root = ensure_root(drive, user_id)
    except (DriveError, SignInError) as exc:
        logger.warning("drive: root of %s failed: %s", user_id, exc, exc_info=True)
        report.failed.append(f"{_owner_label(user_id)}: {exc}")
        return
    folders = store.account_folders()
    for target in _missing(targets, folders):
        try:
            _sync_one(drive, root, target, folders.get(target.login_id), report)
        except (DriveError, SignInError) as exc:
            logger.warning(
                "drive: %s not synced: %s", target.handle, exc, exc_info=True
            )
            report.failed.append(f"{target.folder_name}: {exc}")
    va_folders.sync(drive, root, user_id, report)
    _tally(user_id, targets, report)


def sync(owner: int | None = None) -> SyncReport:
    report = SyncReport()
    if read_live_mode():
        logger.info("drive: live-read mode, sweep skipped (no writes to live)")
        return report
    with store.sync_lock() as held:
        if not held:
            report.busy = True
            return report
        by_owner = {o.user_id: o.targets for o in _by_owner(store.targets(owner))}
        for va_owner in va_folders.owners(owner):
            by_owner.setdefault(va_owner, [])
        for user_id, targets in by_owner.items():
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
    if card.account_folder_id is None:
        return
    try:
        drive = person_drive(user_id, interactive=True)
        if drive is None:
            return
        folder_id = card.account_folder_id
        card.scan = _scan(drive, folder_id, card.posted_folder_id).scan
    except FolderGoneError:  # silent: deleted by hand: the account is removed
        _deleted_by_hand(card.target)
        card.account_folder_id = card.posted_folder_id = None
        card.error = "The account's folder was deleted: the account was removed"
        return
    except (DriveError, SignInError) as exc:
        logger.warning(
            "drive: listing %s failed: %s", card.target.handle, exc, exc_info=True
        )
        card.error = "Drive could not be read"
        return
    store.record_posted(card.target.login_id, folder_id, card.scan.posted, seen_at)
    store.save_scans({card.target.login_id: card.scan}, seen_at)


def _root_folders(user_id: int, drive: DriveApi, root: str) -> list[DriveFile]:

    def check() -> None:
        other = person_drive(user_id, interactive=True)
        if other is None:
            return
        current = other.folder(root)
        if not _is_root(current):
            msg = f"Root {root} is {current.name!r}, not {root_name()!r}"
            raise FolderGoneError(msg)

    with ThreadPoolExecutor(max_workers=1) as pool:
        checked = pool.submit(in_this_trace(check))
        listed = drive.children(root, folders_only=True)
        checked.result()
    return listed


def _list_batched(
    drive: DriveApi, cards: list[DriveCard], names: dict[str, str], seen_at: float
) -> None:
    ids = [c.account_folder_id for c in cards if c.account_folder_id]
    inside = drive.children_of(ids)
    posted_ids = [
        d.id
        for c in cards
        for d in _posted_dirs(inside.of(c.account_folder_id or ""), c.posted_folder_id)
    ]
    posted_items = drive.children_of(posted_ids)
    for card in cards:
        folder_id = card.account_folder_id or ""
        card.scan = _build_scan(
            names[folder_id], inside.of(folder_id), card.posted_folder_id, posted_items
        )
        store.record_posted(card.target.login_id, folder_id, card.scan.posted, seen_at)
    store.save_scans({c.target.login_id: c.scan for c in cards}, seen_at)


def _list_each(user_id: int, cards: list[DriveCard], seen_at: float) -> None:
    with ThreadPoolExecutor(max_workers=settings.gdrive.list_workers) as pool:
        listed = {
            pool.submit(in_this_trace(_list_account), user_id, card, seen_at): card
            for card in cards
        }
        for future, card in listed.items():
            try:
                future.result()
            except Exception:
                logger.exception("drive: card of %s failed", card.target.handle)
                card.error = "Could not read this folder from Drive"


class _RootListing(BaseModel, frozen=True):
    names: dict[str, str]


def _list_root(
    result: DriveBoard,
    user_id: int,
    drive: DriveApi,
    root: str,
    cards: list[DriveCard],
    known: set[str] | None,
    seen_at: float,
) -> _RootListing | None:
    try:
        listed = _root_folders(user_id, drive, root)
    except FolderGoneError:
        logger.warning(
            "drive: root of %s is gone, the sweep finds it", user_id, exc_info=True
        )
        users.set_drive_root(user_id, None)
        return None
    except Exception:
        logger.exception("drive: root of %s not listed, listing one by one", user_id)
        return _RootListing(names={})
    in_root = {f.id: f.name for f in listed}
    if known is not None:
        ours = {name.casefold() for name in review_folders()}
        result.other_folders = sorted(
            (
                f
                for f in listed
                if f.id not in known and f.name.strip().casefold() not in ours
            ),
            key=lambda f: f.name.lower(),
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
        return _RootListing(names={})
    return _RootListing(names=in_root)


def _list_owner(
    result: DriveBoard,
    user_id: int,
    cards: list[DriveCard],
    known: set[str],
    seen_at: float,
    *,
    others: bool,
) -> None:
    not_connected = (
        f"{_owner_label(user_id)} has not connected Google Drive: their"
        " Account page, Connect Google Drive"
    )
    drive = person_drive(user_id, interactive=True)
    if drive is None:
        for card in cards:
            card.error = not_connected
        return
    user = users.get_user(user_id)
    root = user.drive_root_id if user else None
    listing = (
        _list_root(
            result, user_id, drive, root, cards, known if others else None, seen_at
        )
        if root
        else _RootListing(names={})
    )
    if listing is None:
        for card in cards:
            card.account_folder_id = card.posted_folder_id = None
        return
    _list_each(
        user_id,
        [
            c
            for c in cards
            if c.account_folder_id is not None
            and c.account_folder_id not in listing.names
        ],
        seen_at,
    )


def _saved_board(result: DriveBoard, folders: StoredFolders) -> None:
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
    at = at or now()
    me = users.get_user(viewer) if viewer is not None else None
    result = DriveBoard(
        day=at.date(),
        root_folder_id=(me.drive_root_id or "") if me else "",
        root_name=root_name(),
        connected=bool(me and me.drive_connected),
        posted_folder_name=settings.gdrive.posted_folder_name,
    )
    if not enabled():
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
    result.va_folders = va_folders.of_owner(viewer) if viewer is not None else []
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
        _count_uploaded(result, at)
        return result
    known = {f.folder_id for f in folders.folders}
    known |= {f.folder_id for f in result.va_folders}
    seen_at = at.timestamp()
    grouped: dict[int, list[DriveCard]] = defaultdict(list)
    for card in result.cards:
        grouped[card.target.device.user_id].append(card)
    for user_id, cards in grouped.items():
        _list_owner(result, user_id, cards, known, seen_at, others=user_id == viewer)
    _count_uploaded(result, at)
    return result


def _uploaded_since(card: DriveCard, start: datetime.datetime) -> list[str]:
    return [
        f.id
        for f in (*card.scan.waiting, *card.scan.posted)
        if f.created_time is not None and f.created_time >= start
    ]


def _count_uploaded(result: DriveBoard, at: datetime.datetime) -> None:
    start = _today_start(at)
    today = {c.target.login_id: _uploaded_since(c, start) for c in result.cards}
    ours = set(upload_store.made_here([i for ids in today.values() for i in ids]))
    for card in result.cards:
        ids = today[card.target.login_id]
        clone = sum(1 for file_id in ids if file_id in ours)
        card.uploaded_today = UploadedToday(clone=clone, manual=len(ids) - clone)


def _thumb_cache(file_id: str, px: int) -> Path:
    return previews_dir() / "drive" / f"{file_id}_{px}"


def _cached_thumb(file_id: str, px: int) -> DriveThumb | None:
    base = _thumb_cache(file_id, px)
    if not base.parent.is_dir():
        return None
    for path in base.parent.glob(f"{base.name}.*"):
        return DriveThumb(
            content=path.read_bytes(), media_type=f"image/{path.suffix.lstrip('.')}"
        )
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
) -> DriveThumb | None:
    target = next((t for t in store.targets(owner) if t.login_id == login_id), None)
    folder = store.account_folders().get(login_id) if target else None
    if target is None or folder is None:
        return None
    drive = person_drive(target.device.user_id, interactive=True)
    if drive is None:
        return None
    try:
        item = drive.file(file_id)
    except FolderGoneError:  # silent: gone: no thumbnail
        return None
    if not set(item.parents) & {folder.folder_id, folder.posted_folder_id}:
        logger.warning("drive: preview of %s refused: not in that folder", file_id)
        return None
    px = settings.gdrive.thumb_px
    cached = _cached_thumb(file_id, px)
    if cached is not None:
        return cached
    found = drive.thumbnail(item, px)
    if found is None:
        return None
    _keep_thumb(file_id, px, found.content, found.media_type)
    return found
