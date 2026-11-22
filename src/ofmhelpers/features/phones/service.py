import datetime
from contextlib import contextmanager
from types import MappingProxyType
lazy from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

from ofmhelpers.cache import enqueue
from ofmhelpers.db.devices import store
from ofmhelpers.db.devices.catalog import (
    catalog,
    find_model,
    services,
    tracked_services,
)
from ofmhelpers.db.devices.catalog.schema import Catalog, LoginService
from ofmhelpers.db.devices.models import Device, LoginFields, PhoneQuery
from ofmhelpers.db.devices.schema import (
    ArchivedLogin,
    AssignAccount,
    LoginSecrets,
    PhoneSort,
)
from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.jobs.store import create_job, fail_job
from ofmhelpers.db.tracked_accounts.schema import TrackedAccount
from ofmhelpers.db.tracked_accounts.store import get_account
from ofmhelpers.features.accounts.removal import logins_leaving
from ofmhelpers.features.accounts.tracker import track_login
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.phones import workbook
from ofmhelpers.features.phones.catalog.render import render as draw
from ofmhelpers.features.refusals import (
    ConflictError,
    NotFoundError,
    duplicate_refused,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.devices.models import DeviceFields

logger = get_logger(__name__)

DRIVE_SYNC_TASK = drive_folders.SYNC_TASK


class SecretsUnreadableError(RuntimeError): ...


@contextmanager
def _vault() -> Iterator[None]:
    try:
        yield
    except VaultError as exc:
        raise SecretsUnreadableError(str(exc)) from exc


class Assigning(BaseModel):
    account: TrackedAccount
    service: str


class PageView(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    assigning: Assigning | None
    devices: list[Device]
    retired: list[Device]
    archived: list[ArchivedLogin]
    total: int
    logins: int
    owners: list[str]
    sorts: type[PhoneSort]
    default_sort: PhoneSort
    services: MappingProxyType[str, LoginService]
    catalog: Catalog


class Workbook(BaseModel):
    content: bytes
    name: str
    phones: int
    logins: int


class LoginAdded(BaseModel):
    id: int
    linked: bool


def _assigning(account_id: int | None, scope: int) -> Assigning | None:
    account = get_account(account_id, scope) if account_id is not None else None
    service = tracked_services().get(account.platform) if account else None
    if account is None or service is None:
        return None
    return Assigning(account=account, service=service.key)


def page_view(query: PhoneQuery, scope: int) -> PageView:
    devices = store.list_devices(scope)
    active = [d for d in devices if not d.retired]
    return PageView(
        assigning=_assigning(query.assign, scope),
        devices=query.ordered([d for d in active if query.matches(d)]),
        retired=query.ordered([d for d in devices if d.retired]),
        archived=store.archived_logins(scope),
        total=len(active),
        logins=sum(len(d.logins) for d in active),
        owners=sorted({d.owner for d in active if d.owner}, key=str.lower),
        sorts=PhoneSort,
        default_sort=PhoneQuery().sort,
        services=services(),
        catalog=catalog(),
    )


def drawing(model: str, color: str) -> str:
    found = find_model(model)
    phone = found.model if found else None
    finish = catalog().color(phone, color) if phone else None
    return draw(phone, finish)


@_vault()
def export(scope: int) -> Workbook:
    active = [d for d in store.export_devices(scope) if not d.retired]
    today = datetime.datetime.now(tz=datetime.UTC).date()
    return Workbook(
        content=workbook.build(active),
        name=f"phones-{today.isoformat()}.xlsx",
        phones=len(active),
        logins=sum(len(d.logins) for d in active),
    )


@_vault()
def add(body: DeviceFields, owner_id: int) -> int:
    with duplicate_refused():
        return store.add_device(body, owner_id)


def _require(found: bool, what: str) -> None:
    if not found:
        msg = f"{what} not found"
        raise NotFoundError(msg)


def _track(login_id: int, body: LoginFields, *, revive: bool) -> None:
    owner = store.owner_of_login(login_id)
    if owner is not None:
        track_login(body.service, body.username, user_id=owner, revive=revive)


def _make_drive_folder(login_id: int, service: str) -> None:
    found = services().get(service)
    if found is None or not found.tracked_platform or not drive_folders.enabled():
        return
    owner = store.owner_of_login(login_id)
    if owner is None:
        return
    job_id = create_job(DRIVE_SYNC_TASK, {"owner": owner}, actor=str(owner))
    try:
        enqueue(run_job, job_id, drive_folders.sync, {"owner": owner})
    except Exception:
        logger.exception("queueing the Drive sync for login %s failed", login_id)
        fail_job(job_id, "Could not queue the Drive sync")


class Phone(BaseModel, frozen=True):
    scope: int
    device_id: int

    @_vault()
    def edit(self, body: DeviceFields) -> None:
        with duplicate_refused():
            found = store.update_device(self.device_id, body, self.scope)
        _require(found, "Phone")

    def retire(self) -> None:
        _require(store.retire_device(self.device_id, self.scope), "Phone")

    def restore(self) -> None:
        _require(store.restore_device(self.device_id, self.scope), "Phone")

    def delete(self) -> None:
        logins_leaving(self.scope, self.device_id)
        _require(store.delete_device(self.device_id, self.scope), "Phone")

    def add_login(self, body: LoginFields) -> LoginAdded:
        added = self._saved(body)
        _make_drive_folder(added.id, body.service)
        return added

    @_vault()
    def _saved(self, body: LoginFields) -> LoginAdded:
        with duplicate_refused():
            added = store.add_login(self.device_id, body, self.scope)
        if added is None:
            msg = "Phone not found"
            raise NotFoundError(msg)
        _track(added.id, body, revive=True)
        return LoginAdded(id=added.id, linked=added.existed)

    def assign(self, body: AssignAccount) -> LoginAdded:
        found = _assigning(body.account_id, self.scope)
        phone = store.get_device(self.device_id, self.scope)
        if found is None or phone is None:
            msg = "Account or phone not found"
            raise NotFoundError(msg)
        if phone.retired:
            msg = f"{phone.label} is retired: restore it first"
            raise ConflictError(msg)
        new = LoginFields(service=found.service, username=found.account.handle)
        on_phone = phone.accounts_of(found.service)
        already = on_phone.get(new.username)
        others = [name for name in on_phone if name != new.username]
        if others and not body.replace:
            names = ", ".join(f"@{name}" for name in others)
            msg = f"{phone.label} already has {names}"
            raise ConflictError(msg)
        added = LoginAdded(id=already.id, linked=True) if already else self._saved(new)
        for name in others:
            self.remove_login(on_phone[name].id)
        _make_drive_folder(added.id, found.service)
        return added

    def remove_login(self, login_id: int) -> None:
        logins_leaving(self.scope, self.device_id, login_id)
        _require(store.remove_login(self.device_id, login_id, self.scope), "Login")


class SavedLogin(BaseModel, frozen=True):
    scope: int
    login_id: int

    @_vault()
    def secret(self) -> LoginSecrets:
        found = store.secrets(self.login_id, self.scope)
        if found is None:
            msg = "Login not found"
            raise NotFoundError(msg)
        return found

    @_vault()
    def edit(self, body: LoginFields) -> None:
        with duplicate_refused():
            found = store.update_login(self.login_id, body, self.scope)
        _require(found, "Login")
        _track(self.login_id, body, revive=False)

    def restore(self) -> None:
        archived = {x.id: x for x in store.archived_logins(self.scope)}
        if self.login_id in archived and not archived[self.login_id].on:
            msg = "No phone has this account any more: add it to a phone instead"
            raise ConflictError(msg)
        found = store.restore_login(self.login_id, self.scope)
        if found is None:
            msg = "Archived login not found"
            raise NotFoundError(msg)
        owner = store.owner_of_login(self.login_id)
        if owner is not None:
            track_login(found.service, found.username, user_id=owner, revive=True)
