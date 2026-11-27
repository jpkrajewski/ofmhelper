# Copyright (C) 2026 jpkrajewski

from functools import lru_cache

from ofmhelpers.db.devices.catalog import services
from ofmhelpers.db.devices.models import AccountPhones, PhonesByAccount
from ofmhelpers.db.devices.repository import DeviceRepository
from ofmhelpers.db.tracked_accounts.models import names_of
from ofmhelpers.db.tracked_accounts.store import list_accounts
from ofmhelpers.db.users.store import agency_id
from ofmhelpers.utils.secrets.schemas import Revealed
lazy from ofmhelpers.db.devices.models import (
    Device,
    DeviceFields,
    LoginFields,
    LoginSecrets,
)
lazy from ofmhelpers.db.devices.schema import AddedLogin, ArchivedLogin
lazy from ofmhelpers.db.tracked_accounts.schema import TrackedAccount


@lru_cache(maxsize=1)
def _repository() -> DeviceRepository:
    return DeviceRepository()


def _service_rank(service: str) -> int:
    order = list(services())
    return order.index(service) if service in order else len(order)


def _handle(platform: str, username: str) -> str:
    try:
        return names_of(platform).handle(username)
    except KeyError, ValueError:  # silent: not a handle: kept as typed
        return username


def _linked(
    devices: list[Device],
    accounts: list[TrackedAccount] | None = None,
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
                    ),
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
    accounts: list[TrackedAccount],
    owner: int | None = None,
) -> PhonesByAccount:
    found: dict[int, list[Device]] = {}
    for device in _linked(_repository().list_all(owner), accounts):
        if device.retired:
            continue
        ids = {x.tracked_account_id for x in device.logins}
        for account_id in sorted(i for i in ids if i is not None):
            found.setdefault(account_id, []).append(device)
    return PhonesByAccount(
        rows=[AccountPhones(account_id=k, phones=v) for k, v in found.items()],
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
    device_id: int,
    fields: DeviceFields,
    owner: int | None = None,
) -> bool:
    return _repository().update_device(device_id, fields.model_dump(), owner)


def retire_device(device_id: int, owner: int | None = None) -> bool:
    return _repository().set_retired(device_id, retired=True, owner=owner)


def restore_device(device_id: int, owner: int | None = None) -> bool:
    return _repository().set_retired(device_id, retired=False, owner=owner)


def delete_device(device_id: int, owner: int | None = None) -> bool:
    return _repository().delete_device(device_id, owner)


def add_login(
    device_id: int,
    fields: LoginFields,
    owner: int | None = None,
) -> AddedLogin | None:
    return _repository().add_login(
        device_id,
        fields.model_dump(context=Revealed(reason="login added to a phone")),
        owner,
    )


def update_login(login_id: int, fields: LoginFields, owner: int | None = None) -> bool:
    return _repository().update_login(
        login_id,
        fields.model_dump(context=Revealed(reason="login edited")),
        owner,
    )


def archive_logins(login_ids: list[int], owner: int | None = None) -> int:
    return _repository().archive_logins(login_ids, owner)


def restore_login(login_id: int, owner: int | None = None) -> ArchivedLogin | None:
    return _repository().restore_login(login_id, owner)


def archived_logins(owner: int | None = None) -> list[ArchivedLogin]:
    return _repository().archived(owner)


def remove_login(device_id: int, login_id: int, owner: int | None = None) -> bool:
    return _repository().unlink_login(device_id, login_id, owner)
