# Copyright (C) 2026 jpkrajewski

import datetime
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Never
from zoneinfo import ZoneInfo
lazy from pathlib import Path

from pydantic import BaseModel, SkipValidation

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.drive_folders import store
from ofmhelpers.db.drive_folders.models import DriveBoard, DriveCard, DriveTarget
from ofmhelpers.db.drive_folders.schema import (
    AccountScan,
    DriveFile,
    PostedSeen,
    StoredFolder,
    SyncReport,
    UploadedToday,
)
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.schema import JobStatus
from ofmhelpers.db.jobs.store import create_job, fail_job, latest_job
from ofmhelpers.db.tracked_accounts.store import untrack
from ofmhelpers.db.users import store as users
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.previews.render import previews_dir, write_atomic
from ofmhelpers.features.storage.constants import StorageConstants, StorageTask
from ofmhelpers.features.storage.models import BotRoot, _Scanned
from ofmhelpers.features.storage.schema import _AccountFolder, _RootListing
from ofmhelpers.features.storage.va_folders import StorageServiceVaFolders
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.storage import (
    DriveAuthError,
    DriveError,
    DriveFolder,
    DriveListing,
    DriveThumb,
    FolderGoneError,
    FolderItems,
    StorageClient,
)
from ofmhelpers.log import get_logger
from ofmhelpers.monitoring.telemetry import in_this_trace
lazy from ofmhelpers.config.types import AccountFolder
lazy from ofmhelpers.db.drive_folders.schema import StoredFolders
lazy from ofmhelpers.features.storage import jobs

logger = get_logger(__name__)


def enabled() -> bool:
    return settings.signin.google_configured or settings.infra.fake_externals


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
        "drive: folder of %s deleted by hand: removing the account",
        target.handle,
    )
    store.forget_account_folder(target.login_id)
    owner, login_id = target.device.user_id, target.login_id
    tracked = sorted(
        {
            login.tracked_account_id
            for phone in devices.list_devices(owner)
            for login in phone.logins
            if login.id == login_id and login.tracked_account_id
        },
    )
    devices.archive_logins([login_id], owner)
    if tracked:
        untrack(tracked, owner)
    logger.info(
        "drive folder of login %s deleted by hand: removed everywhere",
        login_id,
    )


class _RemovedByHandError(Exception): ...


class _LoginGoneError(Exception): ...


def _gone_meanwhile(target: DriveTarget) -> Never:
    logger.info("drive: %s gone meanwhile, its folder left as it is", target.handle)
    raise _LoginGoneError


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


def _kept_posted_ok(scan: AccountScan, stored: StoredFolder) -> bool:
    name = settings.gdrive.posted_folder_name
    return any(
        d.id == stored.posted_folder_id and d.name == name for d in scan.posted_folders
    )


def _merge(into: SyncReport, part: SyncReport) -> None:
    into.created += part.created
    into.adopted += part.adopted
    into.renamed += part.renamed
    into.posted_folders += part.posted_folders
    into.newly_posted += part.newly_posted
    into.failed += part.failed


def _record_posted(seen: list[PostedSeen], seen_at: float) -> None:
    if store.record_posted(seen, seen_at) is not None:
        return
    gone = seen
    if len(seen) > 1:
        gone = [one for one in seen if store.record_posted([one], seen_at) is None]
    for one in gone:
        logger.info(
            "drive: login %s went meanwhile, its %s posted files are not kept",
            one.login_id,
            len(one.files),
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
            content=path.read_bytes(),
            media_type=f"image/{path.suffix.lstrip('.')}",
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


class StorageServiceFolders(BaseModel, frozen=True, arbitrary_types_allowed=True):
    client: SkipValidation[StorageClient] | None
    owner_id: int
    read_live: bool
    constants: StorageConstants
    bot: BotRoot | None
    va_folders: StorageServiceVaFolders

    def _drive(self) -> StorageClient:
        if self.client is None:
            raise DriveAuthError
        return self.client

    def web_url(self, item_id: str) -> str:
        return self._drive().web_url(item_id)

    def open_url(self, item_id: str) -> str:
        try:
            return self.web_url(item_id)
        except DriveAuthError, SignInError:
            logger.warning(
                "drive: no sign-in to open %s for %s",
                item_id,
                self.owner_id,
                exc_info=True,
            )
            return self.constants.account_page
        except DriveError:
            logger.warning(
                "drive: folder %s of %s not opened",
                item_id,
                self.owner_id,
                exc_info=True,
            )
            return self.constants.drive_page

    def root_name(self) -> str:
        live = settings.infra.production or self.read_live
        c = self.constants
        return c.root_name if live else f"{c.dev_prefix}{c.root_name}"

    def owner_label(self) -> str:
        user = users.get_user(self.owner_id)
        if user:
            return user.email
        return self.constants.unknown_owner.format(user_id=self.owner_id)

    def _is_root(self, folder: DriveFolder) -> bool:
        if settings.infra.production or self.read_live:
            return True
        return folder.name.strip().casefold() == self.root_name().casefold()

    def _syncing_job_id(self, viewer: int | None) -> str | None:
        if viewer is None:
            return None
        job = latest_job(StorageTask.SYNC, str(self.owner_id))
        return job.id if job is not None and job.status is JobStatus.running else None

    def _board_head(self, at: datetime.datetime, viewer: int | None) -> DriveBoard:
        me = users.get_user(viewer) if viewer is not None else None
        result = DriveBoard(
            day=at.date(),
            root_folder_id=(me.drive_root_id or "") if me else "",
            root_name=self.root_name(),
            connected=bool(me and me.drive_connected),
            posted_folder_name=settings.gdrive.posted_folder_name,
            syncing_job_id=self._syncing_job_id(viewer),
        )
        if not enabled():
            result.problem = self.constants.not_set_up
            return result
        if me is not None and not me.drive_connected and self.bot is None:
            result.problem = self.constants.connect_once.format(root=self.root_name())
            result.connect_drive = True
        return result

    def _board_cards(
        self,
        result: DriveBoard,
        folders: StoredFolders,
        viewer: int | None,
    ) -> None:
        result.va_folders = self.va_folders.of_owner() if viewer is not None else []
        result.cards = [
            DriveCard(
                target=t,
                account_folder_id=f.folder_id
                if (f := folders.get(t.login_id))
                else None,
                posted_folder_id=f.posted_folder_id if f else None,
            )
            for t in store.targets(self.owner_id)
        ]

    def saved_board(
        self,
        viewer: int | None = None,
        at: datetime.datetime | None = None,
    ) -> DriveBoard:
        at = at or now()
        result = self._board_head(at, viewer)
        if not enabled():
            return result
        folders = store.account_folders()
        self._board_cards(result, folders, viewer)
        _saved_board(result, folders)
        _count_uploaded(result, at)
        return result

    def usable_root(self) -> str | None:
        user = users.get_user(self.owner_id)
        stored = user.drive_root_id if user else None
        if not stored:
            return None
        try:
            current = self._drive().folder(stored)
        except FolderGoneError:
            logger.warning("drive: root of %s is gone", self.owner_id, exc_info=True)
            return None
        if not self._is_root(current):
            logger.warning(
                "drive: stored root of %s is %r, not %s: not used",
                self.owner_id,
                current.name,
                self.root_name(),
            )
            return None
        return stored

    def _named_root(self) -> str | None:
        wanted = self.root_name().casefold()
        named = [
            f
            for f in self._drive().children("root", folders_only=True)
            if f.name.strip().casefold() == wanted
        ]
        return named[-1].id if named else None

    def find_root(self) -> str | None:
        stored = self.usable_root()
        if stored:
            return stored
        if self.bot is not None:
            return None
        return self._named_root()

    def ensure_root(self) -> str:
        stored = self.usable_root()
        if stored:
            return stored
        if self.bot is not None:
            named = (
                ""
                if settings.infra.production
                else self.constants.bot_root_name_it.format(root=self.root_name())
            )
            msg = self.constants.bot_root_unusable.format(
                email=self.bot.email,
                named=named,
            )
            raise DriveError(msg)
        found = (
            self._named_root()
            or self._drive().create_folder("root", self.root_name()).id
        )
        users.set_drive_root(self.owner_id, found)
        logger.info(
            "drive: root of %s is %s (%s)",
            self.owner_label(),
            found,
            self.root_name(),
        )
        return found

    def trash_folders(self, login_ids: list[int]) -> None:
        stored = store.account_folders()
        folders = [f for i in login_ids if (f := stored.get(i)) is not None]
        for folder in folders:
            try:
                if self.client is not None:
                    self.client.trash_file(folder.folder_id)
            except FolderGoneError:  # silent: already gone
                pass
            except DriveError, SignInError:
                logger.warning(
                    "drive: trashing %s of %s failed",
                    folder.folder_id,
                    self.owner_id,
                    exc_info=True,
                )
                continue
            store.forget_account_folder(folder.login_id)

    def _account_folder(
        self,
        root: str,
        target: DriveTarget,
        stored: StoredFolder | None,
        report: SyncReport,
    ) -> _AccountFolder:
        drive = self._drive()
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
                    target.login_id,
                    current.id,
                    target.folder_name,
                    root,
                ):
                    _gone_meanwhile(target)
                return _AccountFolder(folder_id=current.id, kept=True)
        found = drive.find_folder(root, target.folder_name)
        adopted = found is not None
        if found is None:
            found = drive.create_folder(root, target.folder_name)
        if not store.save_account_folder(
            target.login_id,
            found.id,
            target.folder_name,
            root,
        ):
            _gone_meanwhile(target)
        (report.adopted if adopted else report.created).append(target.folder_name)
        return _AccountFolder(folder_id=found.id, kept=False)

    def _find_posted(self, account_id: str) -> DriveFile | None:
        named = [
            f
            for f in self._drive().children(account_id, folders_only=True)
            if _is_posted(f.name)
        ]
        return named[-1] if named else None

    def _posted_folder(
        self,
        target: DriveTarget,
        account_id: str,
        stored_id: str | None,
        report: SyncReport,
    ) -> str:
        drive = self._drive()
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
        found = self._find_posted(account_id)
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
        self,
        root: str,
        target: DriveTarget,
        stored: StoredFolder | None,
        report: SyncReport,
    ) -> None:
        try:
            account = self._account_folder(root, target, stored, report)
        except _RemovedByHandError, _LoginGoneError:  # silent: handled where raised
            return
        posted_id = stored.posted_folder_id if account.kept and stored else None
        self._posted_folder(target, account.folder_id, posted_id, report)

    def _spoofed(self, items: list[DriveFile]) -> list[DriveFile]:
        found: list[DriveFile] = []
        for d in items:
            if d.kind != "folder" or d.name != AccountFolder.SPOOFED:
                continue
            try:
                found += _files(self._drive().children(d.id))
            except FolderGoneError:  # silent: only that SPOOFED folder went
                logger.info("drive: SPOOFED folder %s gone mid-scan", d.id)
        return found

    def _scan(self, account_id: str, posted_id: str | None) -> _Scanned:
        drive = self._drive()
        current = drive.folder(account_id)
        items = drive.children(account_id)
        posted_items = DriveListing()
        for d in _posted_dirs(items, posted_id):
            try:
                posted_items.folders.append(
                    FolderItems(folder_id=d.id, items=drive.children(d.id)),
                )
            except FolderGoneError:  # silent: only that POSTED folder went
                logger.info("drive: POSTED folder %s gone mid-scan", d.id)
        return _Scanned(
            scan=_build_scan(current.name, items, posted_id, posted_items),
            items=items,
        )

    def _tally_one(
        self,
        target: DriveTarget,
        stored: StoredFolder,
        seen_at: float,
    ) -> SyncReport:
        report = SyncReport()
        try:
            scanned = self._scan(stored.folder_id, stored.posted_folder_id)
        except FolderGoneError:  # silent: deleted by hand: the account is removed
            _deleted_by_hand(target)
            return report
        scan = scanned.scan
        if scan.name != target.folder_name:
            self._drive().rename(stored.folder_id, target.folder_name)
            report.renamed.append(target.folder_name)
        if not _kept_posted_ok(scan, stored):
            self._posted_folder(
                target,
                stored.folder_id,
                stored.posted_folder_id,
                report,
            )
        newly = store.record_posted(
            [
                PostedSeen(
                    login_id=target.login_id,
                    folder_id=stored.folder_id,
                    files=scan.posted,
                ),
            ],
            seen_at,
        )
        if newly is None:
            logger.info("drive: %s gone meanwhile, not tallied", target.handle)
            return report
        report.newly_posted = newly
        store.save_scans({target.login_id: scan}, seen_at)
        spoofed = self._spoofed(scanned.items)
        present = {f.id for f in (*scan.waiting, *scan.posted, *spoofed)}
        upload_store.follow_drive(target.login_id, present, seen_at)
        return report

    def _tally(self, targets: list[DriveTarget], report: SyncReport) -> None:
        folders = store.account_folders()
        linked = [(t, f) for t in targets if (f := folders.get(t.login_id))]
        seen_at = time.time()
        with ThreadPoolExecutor(max_workers=settings.gdrive.list_workers) as pool:
            futures = {
                pool.submit(
                    in_this_trace(lambda pair: self._tally_one(*pair, seen_at)),
                    (t, f),
                ): t
                for t, f in linked
            }
            for future, target in futures.items():
                try:
                    _merge(report, future.result())
                except (DriveError, SignInError) as exc:
                    logger.warning(
                        "drive: %s not tallied: %s",
                        target.handle,
                        exc,
                        exc_info=True,
                    )
                    report.failed.append(f"{target.folder_name}: {exc}")

    def sync_owner(self, targets: list[DriveTarget], report: SyncReport) -> None:
        if self.client is None:
            report.not_connected.append(self.owner_label())
            return
        try:
            root = self.ensure_root()
        except (DriveError, SignInError) as exc:
            logger.warning(
                "drive: root of %s failed: %s",
                self.owner_id,
                exc,
                exc_info=True,
            )
            report.failed.append(f"{self.owner_label()}: {exc}")
            return
        folders = store.account_folders()
        for target in _missing(targets, folders):
            try:
                self._sync_one(root, target, folders.get(target.login_id), report)
            except (DriveError, SignInError) as exc:
                logger.warning(
                    "drive: %s not synced: %s",
                    target.handle,
                    exc,
                    exc_info=True,
                )
                report.failed.append(f"{target.folder_name}: {exc}")
        self.va_folders.sync(root, report)
        self._tally(targets, report)

    def _list_account(self, card: DriveCard, seen_at: float) -> None:
        if card.account_folder_id is None:
            return
        try:
            folder_id = card.account_folder_id
            card.scan = self._scan(folder_id, card.posted_folder_id).scan
        except FolderGoneError:  # silent: deleted by hand: the account is removed
            _deleted_by_hand(card.target)
            card.account_folder_id = card.posted_folder_id = None
            card.error = self.constants.account_folder_deleted
            return
        except (DriveError, SignInError) as exc:
            logger.warning(
                "drive: listing %s failed: %s",
                card.target.handle,
                exc,
                exc_info=True,
            )
            card.error = self.constants.drive_unreadable
            return
        _record_posted(
            [
                PostedSeen(
                    login_id=card.target.login_id,
                    folder_id=folder_id,
                    files=card.scan.posted,
                ),
            ],
            seen_at,
        )
        store.save_scans({card.target.login_id: card.scan}, seen_at)

    def _check_root(self, root: str) -> None:
        current = self._drive().folder(root)
        if not self._is_root(current):
            msg = self.constants.root_mismatch.format(
                root_id=root,
                found=current.name,
                expected=self.root_name(),
            )
            raise FolderGoneError(msg)

    def _root_folders(self, root: str) -> list[DriveFile]:
        with ThreadPoolExecutor(max_workers=1) as pool:
            checked = pool.submit(in_this_trace(self._check_root), root)
            listed = self._drive().children(root, folders_only=True)
            checked.result()
        return listed

    def _list_batched(
        self,
        cards: list[DriveCard],
        names: dict[str, str],
        seen_at: float,
    ) -> None:
        drive = self._drive()
        ids = [c.account_folder_id for c in cards if c.account_folder_id]
        inside = drive.children_of(ids)
        posted_ids = [
            d.id
            for c in cards
            for d in _posted_dirs(
                inside.of(c.account_folder_id or ""),
                c.posted_folder_id,
            )
        ]
        posted_items = drive.children_of(posted_ids)
        seen = []
        for card in cards:
            folder_id = card.account_folder_id or ""
            card.scan = _build_scan(
                names[folder_id],
                inside.of(folder_id),
                card.posted_folder_id,
                posted_items,
            )
            seen.append(
                PostedSeen(
                    login_id=card.target.login_id,
                    folder_id=folder_id,
                    files=card.scan.posted,
                ),
            )
        _record_posted(seen, seen_at)
        store.save_scans({c.target.login_id: c.scan for c in cards}, seen_at)

    def _list_each(self, cards: list[DriveCard], seen_at: float) -> None:
        with ThreadPoolExecutor(max_workers=settings.gdrive.list_workers) as pool:
            listed = {
                pool.submit(
                    in_this_trace(lambda one: self._list_account(one, seen_at)),
                    card,
                ): card
                for card in cards
            }
            for future, card in listed.items():
                try:
                    future.result()
                except Exception:
                    logger.exception("drive: card of %s failed", card.target.handle)
                    card.error = self.constants.folder_unreadable

    def _list_root(
        self,
        result: DriveBoard,
        root: str,
        cards: list[DriveCard],
        known: set[str] | None,
        seen_at: float,
    ) -> _RootListing | None:
        try:
            listed = self._root_folders(root)
        except FolderGoneError:
            logger.warning(
                "drive: root of %s is gone, the sweep finds it",
                self.owner_id,
                exc_info=True,
            )
            users.set_drive_root(self.owner_id, None)
            return None
        except Exception:
            logger.exception(
                "drive: root of %s not listed, listing one by one",
                self.owner_id,
            )
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
            self._list_batched(
                [c for c in cards if c.account_folder_id in in_root],
                in_root,
                seen_at,
            )
        except Exception:
            logger.exception("drive: batched listing failed, listing one by one")
            return _RootListing(names={})
        return _RootListing(names=in_root)

    def _list_owner(
        self,
        result: DriveBoard,
        cards: list[DriveCard],
        known: set[str],
        seen_at: float,
        others: bool,  # noqa: FBT001
    ) -> None:
        if self.client is None:
            for card in cards:
                card.error = self.constants.owner_not_connected.format(
                    owner=self.owner_label(),
                )
            return
        user = users.get_user(self.owner_id)
        root = user.drive_root_id if user else None
        listing = (
            self._list_root(result, root, cards, known if others else None, seen_at)
            if root
            else _RootListing(names={})
        )
        if listing is None:
            for card in cards:
                card.account_folder_id = card.posted_folder_id = None
            return
        self._list_each(
            [
                c
                for c in cards
                if c.account_folder_id is not None
                and c.account_folder_id not in listing.names
            ],
            seen_at,
        )

    def board(
        self,
        viewer: int | None = None,
        at: datetime.datetime | None = None,
    ) -> DriveBoard:
        at = at or now()
        result = self._board_head(at, viewer)
        if not enabled():
            return result
        folders = store.account_folders()
        self._board_cards(result, folders, viewer)
        known = {f.folder_id for f in folders.folders}
        known |= {f.folder_id for f in result.va_folders}
        self._list_owner(
            result,
            result.cards,
            known,
            at.timestamp(),
            others=self.owner_id == viewer,
        )
        _count_uploaded(result, at)
        return result

    def thumbnail(self, login_id: int, file_id: str) -> DriveThumb | None:
        target = next(
            (t for t in store.targets(self.owner_id) if t.login_id == login_id),
            None,
        )
        folder = store.account_folders().get(login_id) if target else None
        if target is None or folder is None or self.client is None:
            return None
        try:
            item = self.client.file(file_id)
        except FolderGoneError:  # silent: gone: no thumbnail
            return None
        if not set(item.parents) & {folder.folder_id, folder.posted_folder_id}:
            logger.warning("drive: preview of %s refused: not in that folder", file_id)
            return None
        px = settings.gdrive.thumb_px
        cached = _cached_thumb(file_id, px)
        if cached is not None:
            return cached
        found = self.client.thumbnail(item, px)
        if found is None:
            return None
        _keep_thumb(file_id, px, found.content, found.media_type)
        return found

    def queue_sync(self) -> None:
        params = {"owner": self.owner_id}
        job_id = create_job(StorageTask.SYNC, params, actor=str(self.owner_id))
        try:
            enqueue(run_job, (job_id, jobs.sync, params))
        except Exception:
            logger.exception("queueing the Drive sync of %s failed", self.owner_id)
            fail_job(job_id, self.constants.sync_not_queued)
