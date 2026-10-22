"""Which Drive folders belong to which Instagram login -- its account folder
and the POSTED folder inside it -- and the files seen posted (/drive). Drive
ids only; Drive itself is `ofmhelpers.gdrive`."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.web.db.models import DriveAccountFolderRow, DrivePostedFileRow
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope

if TYPE_CHECKING:
    from collections.abc import Iterator

# pg_advisory_lock key of a /drive sweep (any bigint no other lock uses).
_SYNC_LOCK_KEY = 0x0D21_5E00


class DriveFolderRepository(CachedRepository):
    cache_namespace = "drive_folder"

    @cached
    def account_folders(self, root: str) -> list[dict]:
        """The logins' folders in `root`; rows of any other root are left
        out (their folders belong to another Drive folder)."""
        with session_scope() as s:
            rows = (
                s.execute(
                    select(DriveAccountFolderRow).where(
                        DriveAccountFolderRow.root_folder_id == root
                    )
                )
                .scalars()
                .all()
            )
            return [
                {
                    "login_id": r.login_id,
                    "folder_id": r.folder_id,
                    "name": r.name,
                    "posted_folder_id": r.posted_folder_id,
                }
                for r in rows
            ]

    @cached
    def folder_ids(self, root: str) -> list[str]:
        """Every Drive folder id the app made or adopted in `root` (account
        and POSTED folders): what /drive may show previews from."""
        with session_scope() as s:
            rows = s.execute(
                select(
                    DriveAccountFolderRow.folder_id,
                    DriveAccountFolderRow.posted_folder_id,
                ).where(DriveAccountFolderRow.root_folder_id == root)
            ).all()
            return sorted({i for row in rows for i in row if i})

    @cached
    def posted_counts(self, root: str, since: list[float]) -> dict[str, list[int]]:
        """{login id (str: the cache is JSON): [files first seen posted at
        or after each of `since`]}, for the logins with a folder in `root`."""
        with session_scope() as s:
            columns = [
                func.count().filter(DrivePostedFileRow.first_seen_at >= t)
                for t in since
            ]
            rows = s.execute(
                select(DrivePostedFileRow.login_id, *columns)
                .join(
                    DriveAccountFolderRow,
                    DriveAccountFolderRow.login_id == DrivePostedFileRow.login_id,
                )
                .where(DriveAccountFolderRow.root_folder_id == root)
                .group_by(DrivePostedFileRow.login_id)
            ).all()
            return {str(login_id): list(counts) for login_id, *counts in rows}

    @contextmanager
    def sync_lock(self) -> Iterator[bool]:
        """Whether this caller holds the one sweep lock (Postgres advisory,
        session level): never waits; a crashed holder's connection closing
        frees it, no expiry to outlive."""
        with session_scope() as s:
            held = bool(
                s.execute(select(func.pg_try_advisory_lock(_SYNC_LOCK_KEY))).scalar()
            )
            try:
                yield held
            finally:
                if held:
                    s.execute(select(func.pg_advisory_unlock(_SYNC_LOCK_KEY)))

    @invalidates_cache
    def save_account_folder(
        self, login_id: int, folder_id: str, name: str, root: str
    ) -> None:
        """A changed folder id drops the POSTED id with it: that one was
        inside the old folder."""
        values = {
            "folder_id": folder_id,
            "name": name,
            "root_folder_id": root,
            "updated_at": time.time(),
        }
        kept_posted = case(
            (
                DriveAccountFolderRow.folder_id == folder_id,
                DriveAccountFolderRow.posted_folder_id,
            ),
            else_=None,
        )
        with session_scope() as s:
            s.execute(
                insert(DriveAccountFolderRow)
                .values(login_id=login_id, **values)
                .on_conflict_do_update(
                    index_elements=["login_id"],
                    set_={**values, "posted_folder_id": kept_posted},
                )
            )

    @invalidates_cache
    def forget_account_folder(self, login_id: int) -> None:
        """Its folder is gone from Drive: the next sweep makes or finds one
        again, and its POSTED folder with it."""
        with session_scope() as s:
            s.execute(
                delete(DriveAccountFolderRow).where(
                    DriveAccountFolderRow.login_id == login_id
                )
            )

    @invalidates_cache
    def save_posted_folder(self, login_id: int, folder_id: str | None) -> None:
        """`None` forgets it: the next sweep finds or makes one again."""
        with session_scope() as s:
            s.execute(
                update(DriveAccountFolderRow)
                .where(DriveAccountFolderRow.login_id == login_id)
                .values(posted_folder_id=folder_id, updated_at=time.time())
            )

    @invalidates_cache
    def record_posted(
        self, login_id: int, files: list[tuple[str, str, str]], seen_at: float
    ) -> int:
        """Adds the (id, name, kind) files not seen before; how many were
        new. A file already recorded keeps its first time (a re-run, or it
        went out and back in, never counts twice)."""
        if not files:
            return 0
        with session_scope() as s:
            added = s.execute(
                insert(DrivePostedFileRow)
                .values(
                    [
                        {
                            "file_id": file_id,
                            "login_id": login_id,
                            "name": name[:512],
                            "kind": kind,
                            "first_seen_at": seen_at,
                        }
                        for file_id, name, kind in files
                    ]
                )
                .on_conflict_do_nothing(index_elements=["file_id"])
                .returning(DrivePostedFileRow.file_id)
            ).all()
        return len(added)
