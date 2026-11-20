from functools import lru_cache
lazy from contextlib import AbstractContextManager

from ofmhelpers.db.repositories import DriveFolderRepository
from ofmhelpers.db.stores.devices import list_devices
from ofmhelpers.db.stores.users import list_users
from ofmhelpers.schemas import AccountScan, DriveTarget, StoredFolder
from ofmhelpers.schemas.drive import StoredFolders, VaFolder
lazy from ofmhelpers.schemas.gdrive import DriveFile
lazy from ofmhelpers.schemas.users import User

_SERVICE = "instagram"


@lru_cache(maxsize=1)
def _repository() -> DriveFolderRepository:
    return DriveFolderRepository()


def targets(owner: int | None = None) -> list[DriveTarget]:
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
    return sorted(
        found.values(), key=lambda t: (t.followers is None, -(t.followers or 0))
    )


def _roots() -> dict[int, str]:
    return {u.id: u.drive_root_id for u in list_users() if u.drive_root_id}


def account_folders() -> StoredFolders:
    roots = _roots()
    owners = {t.login_id: t.device.user_id for t in targets()}
    rows = _repository().account_folders(sorted(set(roots.values())))
    found: dict[int, StoredFolder] = {}
    for row in rows:
        owner = owners.get(row.login_id)
        if owner is not None and roots.get(owner) == row.root_folder_id:
            found[row.login_id] = row
    return StoredFolders(folders=list(found.values()))


def va_folders(vas: list[User], *, any_root: bool = False) -> list[VaFolder]:
    if not vas:
        return []
    roots = _roots()
    owner_of = {va.id: va.owner_id for va in vas}
    return [
        f
        for f in _repository().va_folders(sorted(owner_of))
        if (owner := owner_of.get(f.user_id)) is not None
        and (any_root or roots.get(owner) == f.root_folder_id)
    ]


def save_va_folder(folder: VaFolder) -> bool:
    return _repository().save_va_folder(folder)


def save_account_folder(login_id: int, folder_id: str, name: str, root: str) -> bool:
    return _repository().save_account_folder(login_id, folder_id, name, root)


def forget_account_folder(login_id: int) -> None:
    _repository().forget_account_folder(login_id)


def save_posted_folder(login_id: int, folder_id: str) -> None:
    _repository().save_posted_folder(login_id, folder_id)


def forget_posted_folder(login_id: int) -> None:
    _repository().save_posted_folder(login_id, None)


def record_posted(
    login_id: int, folder_id: str, files: list[DriveFile], seen_at: float
) -> int | None:
    rows = [(f.id, f.name, f.kind) for f in files]
    return _repository().record_posted(login_id, folder_id, rows, seen_at)


def save_scans(scans: dict[int, AccountScan], at: float) -> None:
    _repository().save_scans(
        {k: v.model_dump(mode="json", by_alias=True) for k, v in scans.items()}, at
    )


def logins_changed() -> None:
    _repository().logins_changed()


def sync_lock() -> AbstractContextManager[bool]:
    return _repository().sync_lock()
