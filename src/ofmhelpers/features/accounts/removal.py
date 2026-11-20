from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.catalog import services
from ofmhelpers.db.tracked_accounts.store import untrack
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.devices.schema import Device, DeviceLogin

logger = get_logger(__name__)


def _tracked_ids(logins: list[DeviceLogin]) -> list[int]:
    return sorted({x.tracked_account_id for x in logins if x.tracked_account_id})


def _drive_logins(logins: list[DeviceLogin]) -> list[DeviceLogin]:
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
