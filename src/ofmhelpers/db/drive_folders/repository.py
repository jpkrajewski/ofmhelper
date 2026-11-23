# Copyright (C) 2026 jpkrajewski

import time
from contextlib import contextmanager
lazy from collections.abc import Iterator

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from ofmhelpers.db.cached_repository import CachedRepository, cached, invalidates_cache
from ofmhelpers.db.drive_folders.schema import (
    AccountScan,
    PostedSeen,
    StoredFolder,
    VaFolder,
)
from ofmhelpers.db.models import (
    DriveAccountFolderRow,
    DrivePostedFileRow,
    DriveVaFolderRow,
)
from ofmhelpers.db.session import session_scope

_SYNC_LOCK_KEY = 0x0D21_5E00

_LOGIN_FK = ("drive_account_folders_login_id_fkey", "drive_posted_files_login_id_fkey")
_VA_FK = ("drive_va_folders_user_id_fkey",)


def _parent_gone(exc: IntegrityError, constraints: tuple[str, ...]) -> bool:
    text = str(exc.orig)
    return any(name in text for name in constraints)


class DriveFolderRepository(CachedRepository):
    cache_namespace = "drive_folder"
    cache_depends_on = ("device",)

    @cached
    def account_folders(self, roots: list[str]) -> list[StoredFolder]:
        with session_scope() as s:
            rows = (
                s.execute(
                    select(DriveAccountFolderRow).where(
                        DriveAccountFolderRow.root_folder_id.in_(roots),
                    ),
                )
                .scalars()
                .all()
            )
            return [
                StoredFolder(
                    login_id=r.login_id,
                    folder_id=r.folder_id,
                    name=r.name,
                    root_folder_id=r.root_folder_id or "",
                    posted_folder_id=r.posted_folder_id,
                    scan=(
                        None if r.scan is None else AccountScan.model_validate(r.scan)
                    ),
                    scanned_at=r.scanned_at,
                )
                for r in rows
            ]

    @cached
    def va_folders(self, user_ids: list[int]) -> list[VaFolder]:
        with session_scope() as s:
            rows = (
                s.execute(
                    select(DriveVaFolderRow).where(
                        DriveVaFolderRow.user_id.in_(user_ids),
                    ),
                )
                .scalars()
                .all()
            )
            return [
                VaFolder(
                    user_id=r.user_id,
                    folder_id=r.folder_id,
                    name=r.name,
                    root_folder_id=r.root_folder_id,
                )
                for r in rows
            ]

    @invalidates_cache
    def save_va_folder(self, folder: VaFolder) -> bool:
        values = {
            "folder_id": folder.folder_id,
            "name": folder.name,
            "root_folder_id": folder.root_folder_id,
            "updated_at": func.now(),
        }
        try:
            with session_scope() as s:
                s.execute(
                    insert(DriveVaFolderRow)
                    .values(user_id=folder.user_id, **values)
                    .on_conflict_do_update(index_elements=["user_id"], set_=values),
                )
        except IntegrityError as exc:  # silent: the VA is gone, the caller logs it
            if not _parent_gone(exc, _VA_FK):
                raise
            return False
        return True

    @contextmanager
    def sync_lock(self) -> Iterator[bool]:
        with session_scope() as s:
            held = bool(
                s.execute(select(func.pg_try_advisory_lock(_SYNC_LOCK_KEY))).scalar(),
            )
            try:
                yield held
            finally:
                if held:
                    s.execute(select(func.pg_advisory_unlock(_SYNC_LOCK_KEY)))

    @invalidates_cache
    def save_account_folder(
        self,
        login_id: int,
        folder_id: str,
        name: str,
        root: str,
    ) -> bool:
        values = {
            "folder_id": folder_id,
            "name": name,
            "root_folder_id": root,
            "updated_at": time.time(),
        }
        same = DriveAccountFolderRow.folder_id == folder_id
        kept_posted = case((same, DriveAccountFolderRow.posted_folder_id), else_=None)
        try:
            with session_scope() as s:
                s.execute(
                    insert(DriveAccountFolderRow)
                    .values(login_id=login_id, **values)
                    .on_conflict_do_update(
                        index_elements=["login_id"],
                        set_={
                            **values,
                            "posted_folder_id": kept_posted,
                        },
                    ),
                )
        except IntegrityError as exc:  # silent: the login is gone, the caller logs it
            if not _parent_gone(exc, _LOGIN_FK):
                raise
            return False
        return True

    @invalidates_cache
    def save_scans(self, scans: dict[int, dict], at: float) -> None:
        if not scans:
            return
        with session_scope() as s:
            s.execute(
                update(DriveAccountFolderRow),
                [
                    {"login_id": login_id, "scan": scan, "scanned_at": at}
                    for login_id, scan in scans.items()
                ],
            )

    @invalidates_cache
    def forget_account_folder(self, login_id: int) -> None:
        with session_scope() as s:
            s.execute(
                delete(DriveAccountFolderRow).where(
                    DriveAccountFolderRow.login_id == login_id,
                ),
            )

    @invalidates_cache
    def save_posted_folder(self, login_id: int, folder_id: str | None) -> None:
        with session_scope() as s:
            s.execute(
                update(DriveAccountFolderRow)
                .where(DriveAccountFolderRow.login_id == login_id)
                .values(posted_folder_id=folder_id, updated_at=time.time()),
            )

    @invalidates_cache
    def record_posted(self, seen: list[PostedSeen], seen_at: float) -> int | None:
        rows = [
            {
                "file_id": file.id,
                "login_id": folder.login_id,
                "folder_id": folder.folder_id,
                "name": file.name[:512],
                "kind": file.kind,
                "first_seen_at": seen_at,
            }
            for folder in seen
            for file in folder.files
        ]
        if not rows:
            return 0
        try:
            with session_scope() as s:
                added = s.execute(
                    insert(DrivePostedFileRow)
                    .values(rows)
                    .on_conflict_do_nothing(index_elements=["file_id"])
                    .returning(DrivePostedFileRow.file_id),
                ).all()
        except IntegrityError as exc:  # silent: the login is gone, the caller logs it
            if not _parent_gone(exc, _LOGIN_FK):
                raise
            return None
        return len(added)
