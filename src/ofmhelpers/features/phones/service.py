"""What /phones does behind its routes (web/routers/inventory/phones.py):
the list as the page shows it, the export, and what is done to one phone
(`Phone`) or one saved login (`SavedLogin`) of the person asking. A login of
a tracked service (Instagram) also shows on its phone owner's /accounts."""

import datetime
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.stores import devices as store
from ofmhelpers.db.stores.jobs import create_job, fail_job
from ofmhelpers.db.stores.tracked_accounts import get_account
from ofmhelpers.features.accounts.removal import logins_leaving
from ofmhelpers.features.accounts.tracker import track_login
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.phones import workbook
from ofmhelpers.features.phones.catalog.render import render as draw
from ofmhelpers.features.refusals import ConflictError, NotFoundError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import (
    Device,
    LoginFields,
    PhoneQuery,
    PhoneSort,
    TrackedAccount,
)
from ofmhelpers.schemas.devices import ArchivedLogin, AssignAccount, LoginSecrets
from ofmhelpers.schemas.phone_catalog import (
    catalog,
    find_model,
    services,
    tracked_services,
)
from ofmhelpers.schemas.phone_catalog.models import Catalog, LoginService
from ofmhelpers.utils.registry import Registry
lazy from ofmhelpers.schemas import DeviceFields
lazy from ofmhelpers.schemas.devices import DeviceLogin

logger = get_logger(__name__)

# The job row of the Drive sync a login put on a phone starts.
DRIVE_SYNC_TASK = "drive_sync"


class SecretsUnreadableError(RuntimeError):
    """A saved password or backup code could not be read or written: the
    vault key (`OFM_VAULT_KEY`) is missing or changed. The message says
    which; only /phones shows it."""


@contextmanager
def _vault() -> Iterator[None]:
    """What reads or writes a secret on purpose: the vault's own error
    leaves as this feature's."""
    try:
        yield
    except VaultError as exc:
        raise SecretsUnreadableError(str(exc)) from exc


class Assigning(BaseModel):
    """/accounts "Assign phone": the tracked account waiting for a phone and
    the phone-login service it is saved as."""

    account: TrackedAccount
    service: str


class PageView(BaseModel):
    """/phones: each field is a name `phones.html` reads."""

    # Pick mode (`?assign=`): a click on a phone connects this account.
    assigning: Assigning | None
    devices: list[Device]
    retired: list[Device]
    archived: list[ArchivedLogin]
    total: int
    logins: int
    # The add/edit dialog suggests the people already holding a phone.
    owners: list[str]
    sorts: type[PhoneSort]
    default_sort: PhoneSort
    services: Registry[LoginService]
    catalog: Catalog


class Workbook(BaseModel):
    """The export: every active phone and its logins, in plaintext."""

    content: bytes
    name: str
    phones: int
    logins: int


class LoginAdded(BaseModel):
    id: int
    # The account was already saved (on another phone): linked, not copied.
    linked: bool


def _assigning(account_id: int | None, scope: int) -> Assigning | None:
    """The person's own tracked account `account_id`, when its platform is
    one a phone login tracks (Instagram); None for anything else."""
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
    """The phone's back in its color as SVG, drawn from the catalog; a plain
    phone for an unknown model."""
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
    return store.add_device(body, owner_id)


def _require(found: bool, what: str) -> None:
    if not found:
        msg = f"{what} not found"
        raise NotFoundError(msg)


def _track(login_id: int, body: LoginFields, *, revive: bool) -> None:
    """An Instagram (any tracked service's) login shows on its phone owner's
    /accounts."""
    owner = store.owner_of_login(login_id)
    if owner is not None:
        track_login(body.service, body.username, user_id=owner, revive=revive)


def _make_drive_folder(login_id: int, service: str) -> None:
    """A tracked service's login just put on a phone: its owner's Drive
    folders are made now (on the worker), not at the next sweep. Never
    raises: the login is saved already; a failure is logged and the sweep
    makes the folder."""
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
    """One phone of the person asking (`scope`); anything asked of a phone
    that is not theirs, or gone, is `NotFoundError`."""

    scope: int
    device_id: int

    @_vault()
    def edit(self, body: DeviceFields) -> None:
        _require(store.update_device(self.device_id, body, self.scope), "Phone")

    def retire(self) -> None:
        _require(store.retire_device(self.device_id, self.scope), "Phone")

    def restore(self) -> None:
        _require(store.restore_device(self.device_id, self.scope), "Phone")

    def delete(self) -> None:
        """Its logins no other phone has leave /accounts and Drive too."""
        logins_leaving(self.scope, self.device_id)
        _require(store.delete_device(self.device_id, self.scope), "Phone")

    @_vault()
    def add_login(self, body: LoginFields) -> LoginAdded:
        added = store.add_login(self.device_id, body, self.scope)
        if added is None:
            msg = "Phone not found"
            raise NotFoundError(msg)
        _track(added.id, body, revive=True)
        _make_drive_folder(added.id, body.service)
        return LoginAdded(id=added.id, linked=added.existed)

    def assign(self, body: AssignAccount) -> LoginAdded:
        """/accounts "Assign phone": the person's tracked account becomes a
        login on this phone (no password yet), so it gets its Drive folder.
        The phone's other accounts of that service leave it when `replace`
        (as Remove login does: off their last phone they leave /accounts and
        Drive, password deleted); without it they refuse. The new account
        goes on first: a failure half way leaves the phone with both, never
        with none, and the same request sent again finishes it (an account
        already on the phone is no error)."""
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
        others = [name for name in on_phone.names() if name != new.username]
        if others and not body.replace:
            names = ", ".join(f"@{name}" for name in others)
            msg = f"{phone.label} already has {names}"
            raise ConflictError(msg)
        added = self._keep(already) if already else self.add_login(new)
        for name in others:
            self.remove_login(on_phone[name].id)
        return added

    def _keep(self, login: DeviceLogin) -> LoginAdded:
        """The account is on this phone already: nothing saved again, its
        Drive folder still made (the point of Assign phone)."""
        _make_drive_folder(login.id, login.service)
        return LoginAdded(id=login.id, linked=True)

    def remove_login(self, login_id: int) -> None:
        """Off its last phone, the account leaves /accounts and Drive too."""
        logins_leaving(self.scope, self.device_id, login_id)
        _require(store.remove_login(self.device_id, login_id, self.scope), "Login")


class SavedLogin(BaseModel, frozen=True):
    """One saved login of the person asking (`scope`)."""

    scope: int
    login_id: int

    @_vault()
    def secret(self) -> LoginSecrets:
        """Its password and backup codes, for Show / Copy / Edit."""
        found = store.secrets(self.login_id, self.scope)
        if found is None:
            msg = "Login not found"
            raise NotFoundError(msg)
        return found

    @_vault()
    def edit(self, body: LoginFields) -> None:
        _require(store.update_login(self.login_id, body, self.scope), "Login")
        # A renamed handle is tracked; an account removed by hand stays removed
        # (editing a password is no reason to bring it back).
        _track(self.login_id, body, revive=False)

    def restore(self) -> None:
        """An archived account back on its phones, tracked again (its Drive
        folder is made at the next sweep). Not one whose phones were all
        deleted meanwhile: restored, it would sit on no phone and the next
        phone change would delete it, password included."""
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
