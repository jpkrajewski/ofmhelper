"""What /phones does behind its routes (web/routers/inventory/phones.py):
the list as the page shows it, the export, and what is done to one phone
(`Phone`) or one saved login (`SavedLogin`) of the person asking. A login of
a tracked service (Instagram) also shows on its phone owner's /accounts."""

import datetime
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from pydantic import BaseModel

from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.stores import devices as store
from ofmhelpers.features.accounts.removal import logins_leaving
from ofmhelpers.features.accounts.tracker import track_login
from ofmhelpers.features.phones import workbook
from ofmhelpers.features.phones.catalog.render import render as draw
from ofmhelpers.features.refusals import ConflictError, NotFoundError
from ofmhelpers.schemas import Device, PhoneQuery, PhoneSort
from ofmhelpers.schemas.devices import ArchivedLogin, LoginSecrets
from ofmhelpers.schemas.phone_catalog import catalog, find_model, services
from ofmhelpers.schemas.phone_catalog.models import Catalog, LoginService
from ofmhelpers.utils.registry import Registry
lazy from ofmhelpers.schemas import DeviceFields, LoginFields


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


class PageView(BaseModel):
    """/phones: each field is a name `phones.html` reads."""

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


def page_view(query: PhoneQuery, scope: int) -> PageView:
    devices = store.list_devices(scope)
    active = [d for d in devices if not d.retired]
    return PageView(
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


@dataclass(frozen=True)
class Phone:
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
        return LoginAdded(id=added.id, linked=added.existed)

    def remove_login(self, login_id: int) -> None:
        """Off its last phone, the account leaves /accounts and Drive too."""
        logins_leaving(self.scope, self.device_id, login_id)
        _require(store.remove_login(self.device_id, login_id, self.scope), "Login")


@dataclass(frozen=True)
class SavedLogin:
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
