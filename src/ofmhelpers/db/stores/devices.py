"""
ofmhelpers/web/stores/devices.py

Phones and the logins signed in on them (/phones,
routers/inventory/phones.py). Each belongs to a user: `owner` limits a call
to that user's (None: everyone's, for system work; a session always passes its own
id); a new phone takes
`user_id`, its logins their phone's owner. Lists never carry a secret; `secrets` (one
login, on Show / Copy / Edit) and `export_devices` (the Excel file) decrypt
(db/encrypted.py), so a missing or wrong `OFM_VAULT_KEY` raises
`VaultError` there.
"""

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
    """Built on first use rather than at import (constructing it binds a Redis
    connection for its cache)."""
    return DeviceRepository()


def _service_rank(service: str) -> int:
    """Position in `services.json` (iCloud, Instagram, Gmail first); a
    service no longer listed goes last."""
    order = services().names()
    return order.index(service) if service in order else len(order)


def _handle(platform: str, username: str) -> str:
    """The username as that platform stores handles ("@Name" -> "name"),
    as typed when it takes no such form."""
    try:
        return names_of(platform).handle(username)
    except KeyError, ValueError:
        return username


def _linked(
    devices: list[Device], accounts: list[TrackedAccount] | None = None
) -> list[Device]:
    """The phones, logins in service order (ties keep link
    order), each linked to its /accounts row (of `accounts`, default every
    tracked one) when that platform tracks the handle."""
    if accounts is None:
        accounts = list_accounts()
    # A phone links to its own owner's /accounts row of the handle.
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
    """`owner`'s phones (retired too) by label, with their logins -- no
    password or backup code, only whether there is one."""
    return _linked(_repository().list_all(owner), list_accounts(owner))


def get_device(device_id: int, owner: int | None = None) -> Device | None:
    """One of `owner`'s phones by id with its logins (no secret); None when
    there is none. Unlike the list: no /accounts link on a login, nor the
    other phones it is on."""
    return _repository().get(device_id, owner)


def phones_by_account(
    accounts: list[TrackedAccount], owner: int | None = None
) -> PhonesByAccount:
    """`owner`'s active phones signed in to each of `accounts` (/accounts
    shows them in place of a typed note)."""
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
    """`list_devices` with every password and backup code decrypted."""
    return _linked(_repository().export(owner), list_accounts(owner))


def secrets(login_id: int, owner: int | None = None) -> LoginSecrets | None:
    return _repository().secrets(login_id, owner)


def owner_of_login(login_id: int) -> int | None:
    return _repository().owner_of_login(login_id)


def add_device(fields: DeviceFields, user_id: int | None = None) -> int:
    """`user_id`'s phone (None: the agency's, `users.agency_id`). Raises
    `DuplicateError` (a ValueError) for a label the owner already uses."""
    owner = agency_id() if user_id is None else user_id
    return _repository().add_device(fields.model_dump(), owner)


def update_device(
    device_id: int, fields: DeviceFields, owner: int | None = None
) -> bool:
    return _repository().update_device(device_id, fields.model_dump(), owner)


def retire_device(device_id: int, owner: int | None = None) -> bool:
    """Off the list and the export; every login kept."""
    return _repository().set_retired(device_id, retired=True, owner=owner)


def restore_device(device_id: int, owner: int | None = None) -> bool:
    return _repository().set_retired(device_id, retired=False, owner=owner)


def _logins_gone() -> None:
    """A deleted login's Drive folder rows cascade with it: /drive's cache
    must not keep them. Imported here: the Drive store reads phones."""
    from ofmhelpers.db.stores import drive_folders

    drive_folders.logins_changed()


def delete_device(device_id: int, owner: int | None = None) -> bool:
    """For good, with every login no other phone has."""
    deleted = _repository().delete_device(device_id, owner)
    if deleted:
        _logins_gone()
    return deleted


def add_login(
    device_id: int, fields: LoginFields, owner: int | None = None
) -> AddedLogin | None:
    """The login and whether the account was already saved on another of the
    owner's phones; None when the phone does not exist (or is not
    `owner`'s). Raises `DuplicateError` when it is already on this phone,
    or saved with another password."""
    return _repository().add_login(device_id, fields.model_dump(), owner)


def update_login(login_id: int, fields: LoginFields, owner: int | None = None) -> bool:
    """On every phone it is on."""
    return _repository().update_login(login_id, fields.model_dump(), owner)


def archive_logins(login_ids: list[int], owner: int | None = None) -> int:
    """Off every phone, password kept (features/accounts/removal.py)."""
    archived = _repository().archive_logins(login_ids, owner)
    if archived:
        _logins_gone()
    return archived


def restore_login(login_id: int, owner: int | None = None) -> ArchivedLogin | None:
    """Back on its phones; None when not an archived login of `owner`'s."""
    found = _repository().restore_login(login_id, owner)
    if found is None:
        return None
    _logins_gone()
    return found


def archived_logins(owner: int | None = None) -> list[ArchivedLogin]:
    return _repository().archived(owner)


def remove_login(device_id: int, login_id: int, owner: int | None = None) -> bool:
    """Off this phone; deleted for good when no other phone has it."""
    removed = _repository().unlink_login(device_id, login_id, owner)
    if removed:
        _logins_gone()
    return removed
