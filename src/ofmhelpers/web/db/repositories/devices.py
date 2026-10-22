"""Phones (/phones), their logins, and which phone has which. Each phone
and login belongs to a user (`user_id`); every method taking `owner` acts
only on that user's rows (None: everyone's, for system work)."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, StatementError

from ofmhelpers.web.db.encrypted import VaultError
from ofmhelpers.web.db.models import DeviceLoginRow, DeviceRow, LoginRow
from ofmhelpers.web.db.repositories.cached_repository import CachedRepository
from ofmhelpers.web.db.repositories.owner_scope import mine
from ofmhelpers.web.db.session import session_scope

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

_DEVICE_FIELDS = (
    "label",
    "model_id",
    "color_id",
    "custom_model",
    "owner",
    "phone_number",
    "notes",
)
_IDENTITY = ("service", "provider", "username")
_LOGIN_FIELDS = (*_IDENTITY, "notes")
_SECRETS = ("password", "backup_codes")


class DuplicateError(ValueError):
    """A phone label or a login that already exists (or is already on the
    phone)."""


@contextmanager
def _writing(fields: dict) -> Iterator[None]:
    """A broken unique constraint as a `DuplicateError` the page can show;
    a vault failure while encrypting (SQLAlchemy wraps what a column type
    raises) as the `VaultError` itself. Anything else is not ours to
    explain."""
    try:
        yield
    except IntegrityError as exc:
        text = str(exc.orig)
        if "uq_logins_identity" in text:
            msg = f"{fields['username']} is already saved: add it from its phone"
            raise DuplicateError(msg) from exc
        if "uq_devices_owner_label" in text:
            msg = f"A phone called {fields['label']} already exists"
            raise DuplicateError(msg) from exc
        raise
    except StatementError as exc:
        if isinstance(exc.orig, VaultError):
            raise exc.orig from exc
        raise


def _device_dict(row: DeviceRow, logins: list[dict]) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        **{f: getattr(row, f) for f in _DEVICE_FIELDS},
        "added_at": row.added_at,
        "updated_at": row.updated_at,
        "retired_at": row.retired_at,
        "logins": logins,
    }


def _login_columns() -> tuple:
    """A login as a list shows it: no secret is read, only whether there is
    one ("" is stored unencrypted, see web/db/encrypted.py)."""
    password = LoginRow.__table__.c.password
    codes = LoginRow.__table__.c.backup_codes
    return (
        LoginRow.id,
        LoginRow.service,
        LoginRow.provider,
        LoginRow.username,
        LoginRow.notes,
        LoginRow.updated_at,
        (password != "").label("has_password"),
        (codes != "").label("has_codes"),
    )


def _devices(s: Session, logins: dict[int, dict], owner: int | None) -> list[dict]:
    """`owner`'s phones (everyone's for None) by label, each with its logins
    in the order they were linked; a login lists every phone it is on
    (`on`). A login is only ever on its owner's phones."""
    devices = list(
        s.execute(
            select(DeviceRow)
            .where(*mine(DeviceRow.user_id, owner))
            .order_by(DeviceRow.label, DeviceRow.id)
        ).scalars()
    )
    labels = {d.id: d.label for d in devices}
    links = [
        (device_id, login_id)
        for device_id, login_id in s.execute(
            select(DeviceLoginRow.device_id, DeviceLoginRow.login_id).order_by(
                DeviceLoginRow.linked_at, DeviceLoginRow.login_id
            )
        ).all()
        if device_id in labels
    ]
    on: dict[int, list[str]] = {}
    for device_id, login_id in links:
        on.setdefault(login_id, []).append(labels[device_id])
    per_device: dict[int, list[dict]] = {}
    for device_id, login_id in links:
        per_device.setdefault(device_id, []).append(
            {**logins[login_id], "on": on[login_id]}
        )
    return [_device_dict(d, per_device.get(d.id, [])) for d in devices]


def _touch(s: Session, device_ids: list[int], now: float) -> None:
    if device_ids:
        s.execute(
            update(DeviceRow).where(DeviceRow.id.in_(device_ids)).values(updated_at=now)
        )


def _phones_of(s: Session, login_id: int) -> list[int]:
    return list(
        s.execute(
            select(DeviceLoginRow.device_id).where(DeviceLoginRow.login_id == login_id)
        ).scalars()
    )


def _drop_orphans(s: Session) -> None:
    """Deletes every login no phone links any more."""
    linked = exists().where(DeviceLoginRow.login_id == LoginRow.id)
    s.execute(delete(LoginRow).where(~linked))


class DeviceRepository(CachedRepository):
    """No read is `@cached`: the export and `secrets` return decrypted
    passwords, which must never sit in Redis, and the list is one cheap
    query per table. Nothing is cached, so writes bump nothing."""

    cache_namespace = "device"

    def list_all(self, owner: int | None = None) -> list[dict]:
        """`owner`'s phones (retired too) with their logins -- no secret
        read or decrypted, only `has_password` / `has_codes`."""
        with session_scope() as s:
            rows = s.execute(
                select(*_login_columns()).where(*mine(LoginRow.user_id, owner))
            ).mappings()
            return _devices(s, {r["id"]: dict(r) for r in rows}, owner)

    def export(self, owner: int | None = None) -> list[dict]:
        """`list_all` with every password and backup code decrypted."""
        with session_scope() as s:
            logins = {
                r.id: {
                    "id": r.id,
                    **{f: getattr(r, f) for f in (*_LOGIN_FIELDS, *_SECRETS)},
                    "updated_at": r.updated_at,
                    "has_password": bool(r.password),
                    "has_codes": bool(r.backup_codes),
                }
                for r in s.execute(
                    select(LoginRow).where(*mine(LoginRow.user_id, owner))
                ).scalars()
            }
            return _devices(s, logins, owner)

    def secrets(self, login_id: int, owner: int | None = None) -> dict | None:
        with session_scope() as s:
            row = s.get(LoginRow, login_id)
            if row is None or (owner is not None and row.user_id != owner):
                return None
            return {"password": row.password, "backup_codes": row.backup_codes}

    def owner_of_login(self, login_id: int) -> int | None:
        with session_scope() as s:
            row = s.get(LoginRow, login_id)
            return row.user_id if row else None

    def add_device(self, fields: dict, user_id: int) -> int:
        now = time.time()
        with _writing(fields), session_scope() as s:
            row = DeviceRow(**fields, user_id=user_id, added_at=now, updated_at=now)
            s.add(row)
            s.flush()
            return row.id

    def update_device(
        self, device_id: int, fields: dict, owner: int | None = None
    ) -> bool:
        with _writing(fields), session_scope() as s:
            updated = s.execute(
                update(DeviceRow)
                .where(DeviceRow.id == device_id, *mine(DeviceRow.user_id, owner))
                .values(**fields, updated_at=time.time())
                .returning(DeviceRow.id)
            )
            return updated.scalar_one_or_none() is not None

    def set_retired(
        self, device_id: int, *, retired: bool, owner: int | None = None
    ) -> bool:
        now = time.time()
        with session_scope() as s:
            updated = s.execute(
                update(DeviceRow)
                .where(DeviceRow.id == device_id, *mine(DeviceRow.user_id, owner))
                .values(retired_at=now if retired else None, updated_at=now)
                .returning(DeviceRow.id)
            )
            return updated.scalar_one_or_none() is not None

    def delete_device(self, device_id: int, owner: int | None = None) -> bool:
        """The phone and every login only it had; a login another phone
        also has stays there."""
        with session_scope() as s:
            row = s.get(DeviceRow, device_id)
            if row is None or (owner is not None and row.user_id != owner):
                return False
            s.delete(row)
            s.flush()
            _drop_orphans(s)
            return True

    def add_login(
        self, device_id: int, fields: dict, owner: int | None = None
    ) -> tuple[int, bool] | None:
        """(login id, whether it was already saved): an account the phone's
        owner already saved (same service, provider, username) is linked to
        this phone too, never stored twice; a password or codes typed for it
        must match the saved ones (or fill them when it has none). Another
        owner's login of the same account is never touched. None when the
        phone does not exist (or is not `owner`'s)."""
        now = time.time()
        with _writing(fields), session_scope() as s:
            device = s.get(DeviceRow, device_id)
            if device is None or (owner is not None and device.user_id != owner):
                return None
            same = [getattr(LoginRow, f) == fields[f] for f in _IDENTITY]
            row = s.execute(
                select(LoginRow).where(*same, LoginRow.user_id == device.user_id)
            ).scalar_one_or_none()
            existed = row is not None
            if row is None:
                row = LoginRow(
                    **fields, user_id=device.user_id, added_at=now, updated_at=now
                )
                s.add(row)
                s.flush()
            else:
                for name in _SECRETS:
                    typed, saved = fields[name], getattr(row, name)
                    if typed and saved and typed != saved:
                        what = name.replace("_", " ")
                        msg = f"{row.username} is already saved with other {what}: edit it instead"
                        raise DuplicateError(msg)
                    if typed and not saved:
                        setattr(row, name, typed)
                        row.updated_at = now
            linked = s.execute(
                insert(DeviceLoginRow)
                .values(device_id=device_id, login_id=row.id, linked_at=now)
                .on_conflict_do_nothing()
                .returning(DeviceLoginRow.login_id)
            ).scalar_one_or_none()
            if linked is None:
                msg = f"{row.username} is already on this phone"
                raise DuplicateError(msg)
            _touch(s, [device_id], now)
            return row.id, existed

    def update_login(
        self, login_id: int, fields: dict, owner: int | None = None
    ) -> bool:
        """Changes the account on every phone it is on; False when it does
        not exist (or is not `owner`'s)."""
        now = time.time()
        with _writing(fields), session_scope() as s:
            row = s.get(LoginRow, login_id)
            if row is None or (owner is not None and row.user_id != owner):
                return False
            for name, value in fields.items():
                setattr(row, name, value)
            row.updated_at = now
            s.flush()
            _touch(s, _phones_of(s, login_id), now)
            return True

    def unlink_login(
        self, device_id: int, login_id: int, owner: int | None = None
    ) -> bool:
        """Removes the login from the phone; deleted for good when no other
        phone has it. False when it was not on the phone (or the phone is
        not `owner`'s)."""
        now = time.time()
        with session_scope() as s:
            device = s.get(DeviceRow, device_id)
            if device is None or (owner is not None and device.user_id != owner):
                return False
            removed = s.execute(
                delete(DeviceLoginRow)
                .where(
                    DeviceLoginRow.device_id == device_id,
                    DeviceLoginRow.login_id == login_id,
                )
                .returning(DeviceLoginRow.login_id)
            ).scalar_one_or_none()
            if removed is None:
                return False
            _drop_orphans(s)
            _touch(s, [device_id], now)
            return True
