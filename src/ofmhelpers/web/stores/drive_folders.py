"""
ofmhelpers/web/stores/drive_folders.py

/drive's side of the database: the Instagram logins on active phones (the
ones that get a Drive folder), the Drive ids already made for them
(web/drive_folders.py makes them), and the files seen posted.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.config import settings
from ofmhelpers.web.db.repositories import DriveFolderRepository
from ofmhelpers.web.schemas import DriveTarget, StoredFolder
from ofmhelpers.web.stores.devices import list_devices

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from ofmhelpers.gdrive import DriveFile

_SERVICE = "instagram"


@lru_cache(maxsize=1)
def _repository() -> DriveFolderRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return DriveFolderRepository()


def targets() -> list[DriveTarget]:
    """Every Instagram login on an active phone, most followed first (its
    /accounts row; not tracked yet = last), then by phone label; a login on
    several phones once, with the first by label."""
    found: dict[int, DriveTarget] = {}
    for device in list_devices():
        if device.retired:
            continue
        for login in device.logins:
            if login.service == _SERVICE and login.id not in found:
                found[login.id] = DriveTarget(
                    login_id=login.id,
                    handle=login.username,
                    device=device,
                    followers=login.tracked_followers,
                )
    # Stable: equal counts keep the phone-label order.
    return sorted(
        found.values(), key=lambda t: (t.followers is None, -(t.followers or 0))
    )


def _root() -> str:
    """Only the configured root's folders exist for /drive."""
    return settings.gdrive.root_folder_id


def account_folders() -> dict[int, StoredFolder]:
    rows = _repository().account_folders(_root())
    return {r["login_id"]: StoredFolder.model_validate(r) for r in rows}


def save_account_folder(login_id: int, folder_id: str, name: str) -> None:
    """A different folder than the stored one drops its POSTED id too."""
    _repository().save_account_folder(login_id, folder_id, name, _root())


def forget_account_folder(login_id: int) -> None:
    """With its POSTED folder: both are made or found again next sweep."""
    _repository().forget_account_folder(login_id)


def save_posted_folder(login_id: int, folder_id: str) -> None:
    _repository().save_posted_folder(login_id, folder_id)


def forget_posted_folder(login_id: int) -> None:
    _repository().save_posted_folder(login_id, None)


def record_posted(login_id: int, files: list[DriveFile], seen_at: float) -> int:
    """Remembers when each file was first seen posted; how many are new."""
    rows = [(f.id, f.name, f.kind) for f in files]
    return _repository().record_posted(login_id, rows, seen_at)


def posted_counts(since: list[float]) -> dict[int, list[int]]:
    """{login id: [files first seen posted since each of `since`]}."""
    rows = _repository().posted_counts(_root(), since)
    return {int(login_id): counts for login_id, counts in rows.items()}


def folder_ids() -> set[str]:
    """Every account and POSTED folder the app knows (preview authorisation)."""
    return set(_repository().folder_ids(_root()))


def sync_lock() -> AbstractContextManager[bool]:
    """True inside the block when this caller is the one sweep running."""
    return _repository().sync_lock()
