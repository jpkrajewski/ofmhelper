"""
ofmhelpers/web/stores/devices.py

The agency's phones and the logins signed in on them (/phones,
routers/inventory/phones.py). Lists never carry a secret; `secrets` (one
login, on Show / Copy / Edit) and `export_devices` (the Excel file) decrypt
(web/db/encrypted.py), so a missing or wrong `OFM_VAULT_KEY` raises
`VaultError` there.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.phones import services
from ofmhelpers.socials import get_platform
from ofmhelpers.web.db.repositories import DeviceRepository
from ofmhelpers.web.schemas import Device, LoginSecrets
from ofmhelpers.web.stores.tracked_accounts import list_accounts

if TYPE_CHECKING:
    from ofmhelpers.web.schemas import DeviceFields, LoginFields, TrackedAccount


@lru_cache(maxsize=1)
def _repository() -> DeviceRepository:
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return DeviceRepository()


def _service_rank(service: str) -> int:
    """Position in `services.json` (iCloud, Instagram, Gmail first); a
    service no longer listed goes last."""
    order = list(services())
    return order.index(service) if service in order else len(order)


def _handle(platform: str, username: str) -> str:
    """The username as that platform stores handles ("@Name" -> "name"),
    as typed when it takes no such form."""
    try:
        return get_platform(platform).handle(username)
    except (KeyError, ValueError):
        return username


def _linked(
    rows: list[dict], accounts: list[TrackedAccount] | None = None
) -> list[Device]:
    """The rows as `Device`s, logins in service order (ties keep link
    order), each linked to its /accounts row (of `accounts`, default every
    tracked one) when that platform tracks the handle."""
    devices = [Device.model_validate(row) for row in rows]
    if accounts is None:
        accounts = list_accounts()
    tracked = {(a.platform, a.handle): a for a in accounts}
    for device in devices:
        device.logins.sort(key=lambda login: _service_rank(login.service))
        for login in device.logins:
            service = services().get(login.service)
            if service and service.tracked_platform:
                found = tracked.get(
                    (
                        service.tracked_platform,
                        _handle(service.tracked_platform, login.username),
                    )
                )
                if found is not None:
                    login.tracked_account_id = found.id
                    login.tracked_followers = found.followers
    return devices


def list_devices() -> list[Device]:
    """Every phone (retired too) by label, with its logins -- no password
    or backup code, only whether there is one."""
    return _linked(_repository().list_all())


def phones_by_account(accounts: list[TrackedAccount]) -> dict[int, list[Device]]:
    """The active phones signed in to each of `accounts`, by account id
    (/accounts shows them in place of a typed note)."""
    found: dict[int, list[Device]] = {}
    for device in _linked(_repository().list_all(), accounts):
        if device.retired:
            continue
        ids = {x.tracked_account_id for x in device.logins}
        for account_id in sorted(i for i in ids if i is not None):
            found.setdefault(account_id, []).append(device)
    return found


def export_devices() -> list[Device]:
    """`list_devices` with every password and backup code decrypted."""
    return _linked(_repository().export())


def secrets(login_id: int) -> LoginSecrets | None:
    found = _repository().secrets(login_id)
    return LoginSecrets.model_validate(found) if found else None


def add_device(fields: DeviceFields) -> int:
    """Raises `DuplicateError` (a ValueError) for a label already in use."""
    return _repository().add_device(fields.model_dump())


def update_device(device_id: int, fields: DeviceFields) -> bool:
    return _repository().update_device(device_id, fields.model_dump())


def retire_device(device_id: int) -> bool:
    """Off the list and the export; every login kept."""
    return _repository().set_retired(device_id, retired=True)


def restore_device(device_id: int) -> bool:
    return _repository().set_retired(device_id, retired=False)


def delete_device(device_id: int) -> bool:
    """For good, with every login no other phone has."""
    return _repository().delete_device(device_id)


def add_login(device_id: int, fields: LoginFields) -> tuple[int, bool] | None:
    """(login id, whether the account was already saved on another phone);
    None when the phone does not exist. Raises `DuplicateError` when it is
    already on this phone, or saved with another password."""
    return _repository().add_login(device_id, fields.model_dump())


def update_login(login_id: int, fields: LoginFields) -> bool:
    """On every phone it is on."""
    return _repository().update_login(login_id, fields.model_dump())


def remove_login(device_id: int, login_id: int) -> bool:
    """Off this phone; deleted for good when no other phone has it."""
    return _repository().unlink_login(device_id, login_id)
