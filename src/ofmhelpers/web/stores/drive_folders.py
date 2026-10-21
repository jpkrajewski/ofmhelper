"""
ofmhelpers/web/stores/drive_folders.py

/drive's side of the database: the Instagram logins on active phones (the
ones that get a Drive folder) and the Drive ids already made for them
(web/drive_folders.py makes them).
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.web.db.repositories import DriveFolderRepository
from ofmhelpers.web.schemas import DriveTarget, StoredFolder
from ofmhelpers.web.stores.devices import list_devices

if TYPE_CHECKING:
    import datetime
    from contextlib import AbstractContextManager

_SERVICE = "instagram"


@lru_cache(maxsize=1)
def _repository() -> DriveFolderRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return DriveFolderRepository()


def targets() -> list[DriveTarget]:
    """Every Instagram login on an active phone, by phone label; a login
    on several phones once, with the first."""
    found: dict[int, DriveTarget] = {}
    for device in list_devices():
        if device.retired:
            continue
        for login in device.logins:
            if login.service == _SERVICE and login.id not in found:
                found[login.id] = DriveTarget(
                    login_id=login.id, handle=login.username, device=device
                )
    return list(found.values())


def account_folders() -> dict[int, StoredFolder]:
    rows = _repository().account_folders()
    return {r["login_id"]: StoredFolder.model_validate(r) for r in rows}


def day_folders(day: datetime.date) -> dict[int, str]:
    """{login id: that day's folder id}."""
    rows = _repository().day_folders(day.isoformat())
    return {int(login_id): folder_id for login_id, folder_id in rows.items()}


def save_account_folder(login_id: int, folder_id: str, name: str) -> None:
    _repository().save_account_folder(login_id, folder_id, name)


def forget_account_folder(login_id: int) -> None:
    """With its day folders: all are made or found again next sweep."""
    _repository().forget_account_folder(login_id)


def save_day_folder(login_id: int, day: datetime.date, folder_id: str) -> None:
    _repository().save_day_folder(login_id, day.isoformat(), folder_id)


def forget_day_folder(login_id: int, day: datetime.date) -> None:
    _repository().forget_day_folder(login_id, day.isoformat())


def folder_ids() -> set[str]:
    """Every account and day folder the app knows (thumbnail authorisation)."""
    return set(_repository().folder_ids())


def sync_lock() -> AbstractContextManager[bool]:
    """True inside the block when this caller is the one sweep running."""
    return _repository().sync_lock()
