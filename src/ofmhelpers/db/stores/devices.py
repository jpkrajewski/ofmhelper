from functools import lru_cache

from ofmhelpers.db.repositories import DeviceRepository
from ofmhelpers.db.stores.tracked_accounts import list_accounts
from ofmhelpers.db.stores.users import agency_id
from ofmhelpers.schemas.devices import AccountPhones, PhonesByAccount
from ofmhelpers.schemas.phone_catalog import services
from ofmhelpers.schemas.socials import names_of
lazy from ofmhelpers.schemas import (
    ArchivedLogin,
    Device,
    DeviceFields,
    LoginFields,
    LoginSecrets,
    TrackedAccount,
)
lazy from ofmhelpers.schemas.devices import AddedLogin


@lru_cache(maxsize=1)
def _repository() -> DeviceRepository:
    return DeviceRepository()


def _service_rank(service: str) -> int:
    order = services().names()
    return order.index(service) if service in order else len(order)


def _handle(platform: str, username: str) -> str:
    try:
        return names_of(platform).handle(username)
    except KeyError, ValueError:  # silent: not a handle: kept as typed
        return username


def _linked(
    devices: list[Device], accounts: list[TrackedAccount] | None = None
) -> list[Device]:
    if accounts is None:
        accounts = list_accounts()
    tracked = {(a.user_id, a.platform, a.handle): a for a in accounts}
    for device in devices:
        device.logins.sort(key=lambda login: _service_rank(login.service))
        for login in device.logins:
            service = services().get(login.service)
            if service and service.tracked_platform:
                found = tracked.get(
                    (
                        device.user_id,
                        service.tracked_platform,
                        _handle(service.tracked_platform, login.username),
                    )
                )
                if found is not None:
                    login.tracked_account_id = found.id
                    login.tracked_followers = found.followers
    return devices


def list_devices(owner: int | None = None) -> list[Device]:
    return _linked(_repository().list_all(owner), list_accounts(owner))


def get_device(device_id: int, owner: int | None = None) -> Device | None:
    return _repository().get(device_id, owner)


def phones_by_account(
    accounts: list[TrackedAccount], owner: int | None = None
) -> PhonesByAccount:
    found: dict[int, list[Device]] = {}
    for device in _linked(_repository().list_all(owner), accounts):
        if device.retired:
            continue
        ids = {x.tracked_account_id for x in device.logins}
        for account_id in sorted(i for i in ids if i is not None):
            found.setdefault(account_id, []).append(device)
    return PhonesByAccount(
        rows=[AccountPhones(account_id=k, phones=v) for k, v in found.items()]
    )


def export_devices(owner: int | None = None) -> list[Device]:
    return _linked(_repository().export(owner), list_accounts(owner))


def secrets(login_id: int, owner: int | None = None) -> LoginSecrets | None:
    return _repository().secrets(login_id, owner)


def owner_of_login(login_id: int) -> int | None:
    return _repository().owner_of_login(login_id)


def add_device(fields: DeviceFields, user_id: int | None = None) -> int:
    owner = agency_id() if user_id is None else user_id
    return _repository().add_device(fields.model_dump(), owner)


def update_device(
    device_id: int, fields: DeviceFields, owner: int | None = None
) -> bool:
    return _repository().update_device(device_id, fields.model_dump(), owner)


def retire_device(device_id: int, owner: int | None = None) -> bool:
    return _repository().set_retired(device_id, retired=True, owner=owner)


def restore_device(device_id: int, owner: int | None = None) -> bool:
    return _repository().set_retired(device_id, retired=False, owner=owner)


def _logins_gone() -> None:
    from ofmhelpers.db.stores import drive_folders

    drive_folders.logins_changed()


def delete_device(device_id: int, owner: int | None = None) -> bool:
    deleted = _repository().delete_device(device_id, owner)
    if deleted:
        _logins_gone()
    return deleted


def add_login(
    device_id: int, fields: LoginFields, owner: int | None = None
) -> AddedLogin | None:
    return _repository().add_login(device_id, fields.model_dump(), owner)


def update_login(login_id: int, fields: LoginFields, owner: int | None = None) -> bool:
    return _repository().update_login(login_id, fields.model_dump(), owner)


def archive_logins(login_ids: list[int], owner: int | None = None) -> int:
    archived = _repository().archive_logins(login_ids, owner)
    if archived:
        _logins_gone()
    return archived


def restore_login(login_id: int, owner: int | None = None) -> ArchivedLogin | None:
    found = _repository().restore_login(login_id, owner)
    if found is None:
        return None
    _logins_gone()
    return found


def archived_logins(owner: int | None = None) -> list[ArchivedLogin]:
    return _repository().archived(owner)


def remove_login(device_id: int, login_id: int, owner: int | None = None) -> bool:
    removed = _repository().unlink_login(device_id, login_id, owner)
    if removed:
        _logins_gone()
    return removed
