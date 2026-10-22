"""
ofmhelpers/web/stores/drive_folders.py

/drive's side of the database: the Instagram logins on active phones (the
ones that get a Drive folder), the Drive ids already made for them in their
phone owner's own "ALL ACCOUNTS" folder (web/drive_folders.py makes them),
and the files seen posted.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.web.db.repositories import DriveFolderRepository
from ofmhelpers.web.schemas import AccountScan, DriveTarget, StoredFolder
from ofmhelpers.web.stores.devices import list_devices
from ofmhelpers.web.stores.users import list_users

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from ofmhelpers.gdrive import DriveFile

_SERVICE = "instagram"


@lru_cache(maxsize=1)
def _repository() -> DriveFolderRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return DriveFolderRepository()


def targets(owner: int | None = None) -> list[DriveTarget]:
    """Every Instagram login on an active phone (of `owner`'s; everyone's
    for None), most followed first (its /accounts row; not tracked yet =
    last), then by phone label; a login on several phones once, with the
    first by label."""
    found: dict[int, DriveTarget] = {}
    for device in list_devices(owner):
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


def _roots() -> dict[int, str]:
    """Each user's "ALL ACCOUNTS" folder id (their own Drive), by user id."""
    return {u.id: u.drive_root_id for u in list_users() if u.drive_root_id}


def account_folders() -> dict[int, StoredFolder]:
    """Each active login's folder in its phone owner's current root; a row
    of any other root (an old one, or prod's in a dev copy of its database)
    is left out, so the next sweep finds or makes the folder there."""
    roots = _roots()
    owners = {t.login_id: t.device.user_id for t in targets()}
    rows = _repository().account_folders(sorted(set(roots.values())))
    found: dict[int, StoredFolder] = {}
    for row in rows:
        owner = owners.get(row["login_id"])
        if owner is not None and roots.get(owner) == row["root_folder_id"]:
            found[row["login_id"]] = StoredFolder.model_validate(row)
    return found


def save_account_folder(login_id: int, folder_id: str, name: str, root: str) -> None:
    """A different folder than the stored one drops its POSTED id too."""
    _repository().save_account_folder(login_id, folder_id, name, root)


def forget_account_folder(login_id: int) -> None:
    """With its POSTED folder: both are made or found again next sweep."""
    _repository().forget_account_folder(login_id)


def save_posted_folder(login_id: int, folder_id: str) -> None:
    _repository().save_posted_folder(login_id, folder_id)


def forget_posted_folder(login_id: int) -> None:
    _repository().save_posted_folder(login_id, None)


def record_posted(
    login_id: int, folder_id: str, files: list[DriveFile], seen_at: float
) -> int:
    """Remembers when each file was first seen posted, in the account folder
    `folder_id`; how many are new."""
    rows = [(f.id, f.name, f.kind) for f in files]
    return _repository().record_posted(login_id, folder_id, rows, seen_at)


def posted_counts(since: list[float]) -> dict[int, list[int]]:
    """{login id: [files first seen posted since each of `since`]}."""
    rows = _repository().posted_counts(sorted(set(_roots().values())), since)
    return {int(login_id): counts for login_id, counts in rows.items()}


def save_scans(scans: dict[int, AccountScan], at: float) -> None:
    """Each login's folder as just read: what /drive opens with next time."""
    _repository().save_scans(
        {k: v.model_dump(mode="json", by_alias=True) for k, v in scans.items()}, at
    )


def logins_changed() -> None:
    """Call after deleting a login or phone (their folder rows cascade)."""
    _repository().logins_changed()


def sync_lock() -> AbstractContextManager[bool]:
    """True inside the block when this caller is the one sweep running."""
    return _repository().sync_lock()
