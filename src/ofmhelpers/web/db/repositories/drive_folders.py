"""Which Drive folder belongs to which Instagram login, and which days'
folders exist (/drive). Drive ids only; Drive itself is `ofmhelpers.gdrive`."""

from __future__ import annotations

import datetime
import time

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.web.db.models import DriveAccountFolderRow, DriveDayFolderRow
from ofmhelpers.web.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.web.db.session import session_scope


class DriveFolderRepository(CachedRepository):
    cache_namespace = "drive_folder"

    @cached
    def account_folders(self) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(select(DriveAccountFolderRow)).scalars().all()
            return [
                {"login_id": r.login_id, "folder_id": r.folder_id, "name": r.name}
                for r in rows
            ]

    @cached
    def day_folders(self, day: str) -> dict[str, str]:
        """{login id (str: the cache is JSON): folder id} for `day`
        (ISO date)."""
        with session_scope() as s:
            rows = s.execute(
                select(DriveDayFolderRow.login_id, DriveDayFolderRow.folder_id).where(
                    DriveDayFolderRow.day == _date(day)
                )
            ).all()
            return {str(login_id): folder_id for login_id, folder_id in rows}

    @invalidates_cache
    def save_account_folder(self, login_id: int, folder_id: str, name: str) -> None:
        values = {"folder_id": folder_id, "name": name, "updated_at": time.time()}
        with session_scope() as s:
            s.execute(
                insert(DriveAccountFolderRow)
                .values(login_id=login_id, **values)
                .on_conflict_do_update(index_elements=["login_id"], set_=values)
            )

    @invalidates_cache
    def forget_account_folder(self, login_id: int) -> None:
        """Its folder is gone from Drive: the next sweep makes or finds one
        again, and that day's folder with it."""
        with session_scope() as s:
            for table in (DriveDayFolderRow, DriveAccountFolderRow):
                s.execute(delete(table).where(table.login_id == login_id))

    @invalidates_cache
    def save_day_folder(self, login_id: int, day: str, folder_id: str) -> None:
        values = {"folder_id": folder_id, "created_at": time.time()}
        with session_scope() as s:
            s.execute(
                insert(DriveDayFolderRow)
                .values(login_id=login_id, day=_date(day), **values)
                .on_conflict_do_update(index_elements=["login_id", "day"], set_=values)
            )

    @invalidates_cache
    def forget_day_folder(self, login_id: int, day: str) -> None:
        """That day's folder is gone from Drive: the next sweep makes it again."""
        with session_scope() as s:
            s.execute(
                delete(DriveDayFolderRow).where(
                    DriveDayFolderRow.login_id == login_id,
                    DriveDayFolderRow.day == _date(day),
                )
            )


def _date(day: str) -> datetime.date:
    return datetime.date.fromisoformat(day)
