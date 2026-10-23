"""
ofmhelpers/web/account_removal.py

One account is one thing across /accounts, /phones and Drive (2026-10-22:
a VA removed accounts on /accounts, they stayed on the phones, and the
Drive sweep made the folders he deleted again). Removed in any place, it
goes from all three:

- /accounts Remove (`remove_accounts`): its logins leave every phone,
  archived -- password kept, an admin restores them on /phones; its Drive
  folder goes to the trash; tracking stops (history kept).
- /phones, a login off its last phone or a phone deleted (`logins_leaving`,
  before the delete): its folder goes to the trash, tracking stops; the
  login itself is deleted as /phones says.
- Its Drive folder deleted by hand (`folder_deleted`, from the sweep): the
  login is archived, tracking stops; the folder is not made again.

Drive failures never block a removal: logged, the folder stays (/drive
lists it under folders no login owns).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ofmhelpers.log import get_logger
from ofmhelpers.phones import services
from ofmhelpers.web import drive_folders
from ofmhelpers.web.stores import devices
from ofmhelpers.web.stores.tracked_accounts import untrack

if TYPE_CHECKING:
    from ofmhelpers.web.schemas import Device, DeviceLogin

logger = get_logger(__name__)


def _tracked_ids(logins: list[DeviceLogin]) -> list[int]:
    return sorted({x.tracked_account_id for x in logins if x.tracked_account_id})


def _drive_logins(logins: list[DeviceLogin]) -> list[DeviceLogin]:
    """The logins of a service that has an /accounts platform (Instagram):
    the ones with a Drive folder."""
    return [
        x
        for x in logins
        if (info := services().get(x.service)) is not None and info.tracked_platform
    ]


def _unique(phones: list[Device], wanted) -> list[DeviceLogin]:
    seen: dict[int, DeviceLogin] = {}
    for phone in phones:
        for login in phone.logins:
            if wanted(phone, login):
                seen.setdefault(login.id, login)
    return list(seen.values())


def remove_accounts(account_ids: list[int], owner: int) -> int:
    """/accounts Remove: everywhere. How many accounts stopped."""
    wanted = set(account_ids)
    logins = _unique(
        devices.list_devices(owner),
        lambda _phone, login: login.tracked_account_id in wanted,
    )
    ids = [x.id for x in logins]
    drive_folders.trash_folders(owner, ids)
    devices.archive_logins(ids, owner)
    logger.info("removed accounts %s everywhere (logins %s)", sorted(wanted), ids)
    return untrack(account_ids, owner)


def logins_leaving(owner: int, device_id: int, login_id: int | None = None) -> None:
    """Before /phones deletes a phone (`login_id` None) or takes one login
    off it: the logins no other phone has are leaving for good -- their
    folders to the trash, their accounts untracked."""
    phones = devices.list_devices(owner)
    count: dict[int, int] = {}
    for phone in phones:
        for login in phone.logins:
            count[login.id] = count.get(login.id, 0) + 1
    leaving = _drive_logins(
        _unique(
            phones,
            lambda phone, login: (
                phone.id == device_id
                and (login_id is None or login.id == login_id)
                and count[login.id] == 1
            ),
        )
    )
    if not leaving:
        return
    drive_folders.trash_folders(owner, [x.id for x in leaving])
    if tracked := _tracked_ids(leaving):
        untrack(tracked, owner)


def folder_deleted(owner: int, login_id: int) -> None:
    """The sweep found the login's Drive folder deleted by hand: the
    account goes everywhere, its login archived (password kept)."""
    logins = _unique(
        devices.list_devices(owner), lambda _phone, login: login.id == login_id
    )
    devices.archive_logins([login_id], owner)
    if tracked := _tracked_ids(logins):
        untrack(tracked, owner)
    logger.info(
        "drive folder of login %s deleted by hand: removed everywhere", login_id
    )
