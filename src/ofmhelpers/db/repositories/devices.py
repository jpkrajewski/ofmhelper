import time
from contextlib import contextmanager
lazy from collections.abc import Iterator

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import undefer_group
lazy from sqlalchemy.orm import Session

from ofmhelpers.db.encrypted import VaultError
from ofmhelpers.db.models import DeviceLoginRow, DeviceRow, LoginRow
from ofmhelpers.db.repositories.cached_repository import CachedRepository
from ofmhelpers.db.repositories.owner_scope import mine
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.devices import (
    AddedLogin,
    ArchivedLogin,
    Device,
    DeviceLogin,
    LoginSecrets,
)

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


class DuplicateError(ValueError): ...


@contextmanager
def _writing(fields: dict) -> Iterator[None]:
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


def _device(row: DeviceRow, logins: list[DeviceLogin]) -> Device:
    return Device(
        id=row.id,
        user_id=row.user_id,
        **{f: getattr(row, f) for f in _DEVICE_FIELDS},
        added_at=row.created_at.timestamp(),
        updated_at=row.updated_at.timestamp(),
        retired_at=row.retired_at,
        logins=logins,
    )


def _login_columns() -> tuple:
    password = LoginRow.__table__.c.password
    codes = LoginRow.__table__.c.backup_codes
    return (
        LoginRow.id,
        LoginRow.service,
        LoginRow.provider,
        LoginRow.username,
        LoginRow.notes,
        func.extract("epoch", LoginRow.updated_at).label("updated_at"),
        (password != "").label("has_password"),
        (codes != "").label("has_codes"),
    )


def _devices(
    s: Session, logins: dict[int, DeviceLogin], owner: int | None
) -> list[Device]:
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
                DeviceLoginRow.created_at, DeviceLoginRow.login_id
            )
        ).all()
        if device_id in labels and login_id in logins
    ]
    on: dict[int, list[str]] = {}
    for device_id, login_id in links:
        on.setdefault(login_id, []).append(labels[device_id])
    per_device: dict[int, list[DeviceLogin]] = {}
    for device_id, login_id in links:
        per_device.setdefault(device_id, []).append(
            logins[login_id].model_copy(update={"on": list(on[login_id])})
        )
    return [_device(d, per_device.get(d.id, [])) for d in devices]


def _touch(s: Session, device_ids: list[int]) -> None:
    if device_ids:
        s.execute(
            update(DeviceRow)
            .where(DeviceRow.id.in_(device_ids))
            .values(updated_at=func.now())
        )


def _phones_of(s: Session, login_id: int) -> list[int]:
    return list(
        s.execute(
            select(DeviceLoginRow.device_id).where(DeviceLoginRow.login_id == login_id)
        ).scalars()
    )


def _drop_orphans(s: Session, owner_id: int) -> None:
    linked = exists().where(DeviceLoginRow.login_id == LoginRow.id)
    s.execute(
        delete(LoginRow).where(
            ~linked, LoginRow.archived_at.is_(None), LoginRow.user_id == owner_id
        )
    )


class DeviceRepository(CachedRepository):
    cache_namespace = "device"

    def list_all(self, owner: int | None = None) -> list[Device]:
        with session_scope() as s:
            rows = s.execute(
                select(*_login_columns()).where(
                    *mine(LoginRow.user_id, owner), LoginRow.archived_at.is_(None)
                )
            ).mappings()
            return _devices(s, {r["id"]: DeviceLogin(**r) for r in rows}, owner)

    def get(self, device_id: int, owner: int | None = None) -> Device | None:
        with session_scope() as s:
            row = s.get(DeviceRow, device_id)
            if row is None or (owner is not None and row.user_id != owner):
                return None
            logins = s.execute(
                select(*_login_columns())
                .join(DeviceLoginRow, DeviceLoginRow.login_id == LoginRow.id)
                .where(
                    DeviceLoginRow.device_id == device_id,
                    LoginRow.archived_at.is_(None),
                )
                .order_by(DeviceLoginRow.created_at, DeviceLoginRow.login_id)
            ).mappings()
            return _device(row, [DeviceLogin(**r) for r in logins])

    def export(self, owner: int | None = None) -> list[Device]:
        with session_scope() as s:
            logins = {
                r.id: DeviceLogin(
                    id=r.id,
                    **{f: getattr(r, f) for f in (*_LOGIN_FIELDS, *_SECRETS)},
                    updated_at=r.updated_at.timestamp(),
                    has_password=bool(r.password),
                    has_codes=bool(r.backup_codes),
                )
                for r in s.execute(
                    select(LoginRow)
                    .options(undefer_group("secret"))
                    .where(
                        *mine(LoginRow.user_id, owner), LoginRow.archived_at.is_(None)
                    )
                ).scalars()
            }
            return _devices(s, logins, owner)

    def secrets(self, login_id: int, owner: int | None = None) -> LoginSecrets | None:
        with session_scope() as s:
            row = s.get(LoginRow, login_id)
            if row is None or (owner is not None and row.user_id != owner):
                return None
            return LoginSecrets(password=row.password, backup_codes=row.backup_codes)

    def owner_of_login(self, login_id: int) -> int | None:
        with session_scope() as s:
            row = s.get(LoginRow, login_id)
            return row.user_id if row else None

    def add_device(self, fields: dict, user_id: int) -> int:
        with _writing(fields), session_scope() as s:
            row = DeviceRow(**fields, user_id=user_id)
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
                .values(**fields)
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
                .values(retired_at=now if retired else None)
                .returning(DeviceRow.id)
            )
            return updated.scalar_one_or_none() is not None

    def delete_device(self, device_id: int, owner: int | None = None) -> bool:
        with session_scope() as s:
            row = s.get(DeviceRow, device_id)
            if row is None or (owner is not None and row.user_id != owner):
                return False
            owner_id = row.user_id
            s.delete(row)
            s.flush()
            _drop_orphans(s, owner_id)
            return True

    def add_login(
        self, device_id: int, fields: dict, owner: int | None = None
    ) -> AddedLogin | None:
        with _writing(fields), session_scope() as s:
            device = s.get(DeviceRow, device_id)
            if device is None or (owner is not None and device.user_id != owner):
                return None
            same = [getattr(LoginRow, f) == fields[f] for f in _IDENTITY]
            row = s.execute(
                select(LoginRow).where(*same, LoginRow.user_id == device.user_id)
            ).scalar_one_or_none()
            existed = row is not None
            revived = row is not None and row.archived_at is not None
            if row is not None:
                row.archived_at = None
            if row is None:
                row = LoginRow(**fields, user_id=device.user_id)
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
            linked = s.execute(
                insert(DeviceLoginRow)
                .values(device_id=device_id, login_id=row.id)
                .on_conflict_do_nothing()
                .returning(DeviceLoginRow.login_id)
            ).scalar_one_or_none()
            if linked is None and not revived:
                msg = f"{row.username} is already on this phone"
                raise DuplicateError(msg)
            _touch(s, [device_id])
            return AddedLogin(id=row.id, existed=existed)

    def archive_logins(self, login_ids: list[int], owner: int | None = None) -> int:
        if not login_ids:
            return 0
        now = time.time()
        with session_scope() as s:
            done = s.execute(
                update(LoginRow)
                .where(
                    LoginRow.id.in_(login_ids),
                    *mine(LoginRow.user_id, owner),
                    LoginRow.archived_at.is_(None),
                )
                .values(archived_at=now)
                .returning(LoginRow.id)
            ).scalars()
            ids = list(done)
            for login_id in ids:
                _touch(s, _phones_of(s, login_id))
            return len(ids)

    def restore_login(
        self, login_id: int, owner: int | None = None
    ) -> ArchivedLogin | None:
        with session_scope() as s:
            row = s.get(LoginRow, login_id)
            if (
                row is None
                or row.archived_at is None
                or (owner is not None and row.user_id != owner)
            ):
                return None
            row.archived_at = None
            _touch(s, _phones_of(s, login_id))
            return ArchivedLogin(
                id=login_id, service=row.service, username=row.username
            )

    def archived(self, owner: int | None = None) -> list[ArchivedLogin]:
        with session_scope() as s:
            rows = s.execute(
                select(LoginRow)
                .where(
                    *mine(LoginRow.user_id, owner), LoginRow.archived_at.is_not(None)
                )
                .order_by(LoginRow.archived_at.desc())
            ).scalars()
            found = []
            for row in rows:
                labels = s.execute(
                    select(DeviceRow.label)
                    .join(DeviceLoginRow, DeviceLoginRow.device_id == DeviceRow.id)
                    .where(DeviceLoginRow.login_id == row.id)
                    .order_by(DeviceRow.label)
                ).scalars()
                found.append(
                    ArchivedLogin(
                        id=row.id,
                        service=row.service,
                        username=row.username,
                        archived_at=row.archived_at or 0,
                        on=list(labels),
                    )
                )
            return found

    def update_login(
        self, login_id: int, fields: dict, owner: int | None = None
    ) -> bool:
        with _writing(fields), session_scope() as s:
            row = s.get(LoginRow, login_id)
            if row is None or (owner is not None and row.user_id != owner):
                return False
            for name, value in fields.items():
                setattr(row, name, value)
            s.flush()
            _touch(s, _phones_of(s, login_id))
            return True

    def unlink_login(
        self, device_id: int, login_id: int, owner: int | None = None
    ) -> bool:
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
            _drop_orphans(s, device.user_id)
            _touch(s, [device_id])
            return True
