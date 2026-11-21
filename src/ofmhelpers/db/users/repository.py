import hashlib
import time
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from ofmhelpers.db.cached_repository import CachedRepository, cached, invalidates_cache
from ofmhelpers.db.models import (
    PasswordLinkRow,
    UserApiKeyRow,
    UserIdentityRow,
    UserRow,
)
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.users.schema import (
    LinkedAccount,
    Role,
    SavedKey,
    SavedKeys,
    Standing,
    User,
)


def _credential_stamp(password_hash: str) -> str:
    if not password_hash:
        return ""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        name=row.name,
        role=Role(row.role),
        active=row.active,
        created_at=row.created_at.timestamp(),
        last_login_at=row.last_login_at,
        drive_connected=row.drive_connected,
        has_password=bool(row.password_hash),
        drive_root_id=row.drive_root_id,
        is_password_account=row.is_password_account,
        owner_id=row.owner_id,
        credential_stamp=_credential_stamp(row.password_hash),
        sessions_ended_at=(
            row.sessions_ended_at.timestamp() if row.sessions_ended_at else None
        ),
        last_seen_at=row.last_seen_at.timestamp() if row.last_seen_at else None,
    )


def _linked_account(row: UserIdentityRow) -> LinkedAccount:
    return LinkedAccount(
        provider=row.provider,
        subject=row.subject,
        email=row.email,
        linked_at=row.created_at.timestamp(),
    )


class OwnsDataError(ValueError): ...


class UserRepository(CachedRepository):
    cache_namespace = "user"

    def fresh(self, user_id: int) -> User | None:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            return _user(row) if row else None

    def standing(self, user_id: int) -> Standing | None:
        employer = aliased(UserRow)
        employer_active = (
            select(employer.active)
            .where(employer.id == UserRow.owner_id)
            .scalar_subquery()
        )
        with session_scope() as s:
            found = s.execute(
                select(
                    UserRow.role,
                    UserRow.active,
                    UserRow.password_hash,
                    UserRow.sessions_ended_at,
                    employer_active,
                ).where(UserRow.id == user_id)
            ).one_or_none()
        if found is None:
            return None
        role, active, password_hash, ended, employer_is_active = found
        return Standing(
            role=Role(role),
            active=active,
            credential_stamp=_credential_stamp(password_hash),
            sessions_ended_at=ended.timestamp() if ended else None,
            employer_active=employer_is_active,
        )

    @cached
    def get(self, user_id: int) -> User | None:
        return self.fresh(user_id)

    @cached
    def by_email(self, email: str) -> User | None:
        with session_scope() as s:
            row = s.execute(
                select(UserRow).where(UserRow.email == email)
            ).scalar_one_or_none()
            return _user(row) if row else None

    @cached
    def list_all(self) -> list[User]:
        with session_scope() as s:
            rows = s.execute(select(UserRow)).scalars().all()
            found = [_user(r) for r in rows]
        return sorted(found, key=lambda u: (not u.is_admin, u.email))

    @cached
    def password_account(self) -> User | None:
        with session_scope() as s:
            row = s.execute(
                select(UserRow).where(UserRow.is_password_account.is_(True)).limit(1)
            ).scalar_one_or_none()
            return _user(row) if row else None

    @invalidates_cache
    def set_drive_root(self, user_id: int, folder_id: str | None) -> None:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            if row is not None:
                row.drive_root_id = folder_id

    @invalidates_cache
    def add(
        self,
        email: str,
        role: str,
        name: str = "",
        *,
        password_account: bool = False,
        owner_id: int | None = None,
    ) -> User | None:
        with session_scope() as s:
            row = s.scalars(
                insert(UserRow)
                .values(
                    email=email,
                    name=name,
                    role=role,
                    active=True,
                    google_refresh_token="",
                    password_hash="",
                    is_password_account=password_account,
                    owner_id=owner_id,
                )
                .on_conflict_do_nothing(index_elements=[UserRow.email])
                .returning(UserRow)
            ).one_or_none()
            return _user(row) if row else None

    @invalidates_cache
    def update(self, user_id: int, **fields: object) -> User | None:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            if row is None:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            s.flush()
            return _user(row)

    @invalidates_cache
    def end_sessions(self, user_id: int) -> None:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            if row is not None:
                row.sessions_ended_at = datetime.now(tz=UTC)

    @invalidates_cache
    def seen(self, user_id: int, at: float) -> None:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            if row is not None:
                row.last_seen_at = datetime.fromtimestamp(at, tz=UTC)

    @invalidates_cache
    def end_idle_sessions(self, user_id: int) -> bool:
        with session_scope() as s:
            ended = s.execute(
                update(UserRow)
                .where(
                    UserRow.id == user_id,
                    or_(
                        UserRow.sessions_ended_at.is_(None),
                        UserRow.sessions_ended_at < UserRow.last_seen_at,
                    ),
                )
                .values(sessions_ended_at=datetime.now(tz=UTC))
                .returning(UserRow.id)
            )
            return ended.first() is not None

    def idle_vas(self, before: float) -> list[User]:
        cutoff = datetime.fromtimestamp(before, tz=UTC)
        with session_scope() as s:
            rows = s.scalars(
                select(UserRow).where(
                    UserRow.role == Role.VA.value,
                    UserRow.active,
                    UserRow.last_seen_at < cutoff,
                    or_(
                        UserRow.sessions_ended_at.is_(None),
                        UserRow.sessions_ended_at < UserRow.last_seen_at,
                    ),
                )
            ).all()
            return [_user(row) for row in rows]

    @invalidates_cache
    def signed_in(self, user_id: int, name: str, refresh_token: str | None) -> None:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            if row is None:
                return
            row.last_login_at = time.time()
            if name:
                row.name = name
            if refresh_token:
                row.google_refresh_token = refresh_token

    @invalidates_cache
    def delete(self, user_id: int) -> bool:
        try:
            with session_scope() as s:
                row = s.get(UserRow, user_id)
                if row is None:
                    return False
                s.delete(row)
                s.flush()
                return True
        except IntegrityError as exc:
            msg = (
                "still owns jobs, phones, accounts, watched profiles, picture"
                " presets or VAs"
            )
            raise OwnsDataError(msg) from exc

    def refresh_token(self, user_id: int) -> str:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            return row.google_refresh_token if row else ""

    def api_keys(self, user_id: int) -> SavedKeys:
        with session_scope() as s:
            rows = s.execute(
                select(UserApiKeyRow).where(UserApiKeyRow.user_id == user_id)
            ).scalars()
            return SavedKeys(
                keys=[SavedKey(provider=r.provider, api_key=r.api_key) for r in rows]
            )

    def keys_in_slot(self, slot: str) -> list[str]:
        key, user = UserApiKeyRow, UserRow
        with session_scope() as s:
            rows = s.execute(
                select(key)
                .join(user, user.id == key.user_id)
                .where(key.provider == slot, user.active, user.role == Role.ADMIN.value)
                .order_by(key.user_id)
            ).scalars()
            return [r.api_key for r in rows if r.api_key]

    def set_api_key(self, user_id: int, provider: str, api_key: str) -> None:
        with session_scope() as s:
            s.execute(
                delete(UserApiKeyRow).where(
                    UserApiKeyRow.user_id == user_id,
                    UserApiKeyRow.provider == provider,
                )
            )
            if api_key:
                s.add(
                    UserApiKeyRow(
                        user_id=user_id,
                        provider=provider,
                        api_key=api_key,
                    )
                )

    @cached
    def user_for_identity(self, provider: str, subject: str) -> int | None:
        with session_scope() as s:
            row = s.get(UserIdentityRow, (provider, subject))
            return row.user_id if row else None

    @cached
    def identities(self, user_id: int) -> list[LinkedAccount]:
        with session_scope() as s:
            rows = s.execute(
                select(UserIdentityRow)
                .where(UserIdentityRow.user_id == user_id)
                .order_by(UserIdentityRow.provider)
            ).scalars()
            return [_linked_account(r) for r in rows]

    @invalidates_cache
    def link_identity(
        self, user_id: int, provider: str, subject: str, email: str
    ) -> bool:
        with session_scope() as s:
            s.execute(
                insert(UserIdentityRow)
                .values(
                    provider=provider,
                    subject=subject,
                    user_id=user_id,
                    email=email,
                )
                .on_conflict_do_nothing(
                    index_elements=[UserIdentityRow.provider, UserIdentityRow.subject]
                )
            )
            row = s.get(UserIdentityRow, (provider, subject))
            if row is None or row.user_id != user_id:
                return False
            row.email = email
            return True

    @invalidates_cache
    def unlink_identity(self, user_id: int, provider: str) -> None:
        with session_scope() as s:
            s.execute(
                delete(UserIdentityRow).where(
                    UserIdentityRow.user_id == user_id,
                    UserIdentityRow.provider == provider,
                )
            )
            if provider == "google":
                row = s.get(UserRow, user_id)
                if row is not None:
                    row.google_refresh_token = ""

    def password_hash(self, user_id: int) -> str:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            return row.password_hash if row else ""

    @invalidates_cache
    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            if row is None:
                return
            row.password_hash = password_hash
            s.execute(delete(PasswordLinkRow).where(PasswordLinkRow.user_id == user_id))

    @invalidates_cache
    def replace_password_hash(
        self, user_id: int, expected: str, password_hash: str
    ) -> bool:
        with session_scope() as s:
            swapped = s.execute(
                update(UserRow)
                .where(UserRow.id == user_id, UserRow.password_hash == expected)
                .values(password_hash=password_hash)
                .returning(UserRow.id)
            ).scalar_one_or_none()
            if swapped is None:
                return False
            s.execute(delete(PasswordLinkRow).where(PasswordLinkRow.user_id == user_id))
            return True

    def add_password_link(
        self, user_id: int, token_hash: str, expires_at: float
    ) -> None:
        with session_scope() as s:
            s.execute(delete(PasswordLinkRow).where(PasswordLinkRow.user_id == user_id))
            s.add(
                PasswordLinkRow(
                    token_hash=token_hash, user_id=user_id, expires_at=expires_at
                )
            )

    @invalidates_cache
    def use_password_link(
        self, token_hash: str, now: float, password_hash: str
    ) -> int | None:
        with session_scope() as s:
            user_id = s.execute(
                delete(PasswordLinkRow)
                .where(
                    PasswordLinkRow.token_hash == token_hash,
                    PasswordLinkRow.expires_at >= now,
                )
                .returning(PasswordLinkRow.user_id)
            ).scalar_one_or_none()
            row = s.get(UserRow, user_id) if user_id is not None else None
            if row is None or not row.active:
                return None
            row.password_hash = password_hash
            s.execute(delete(PasswordLinkRow).where(PasswordLinkRow.user_id == user_id))
            return user_id

    def link_user(self, token_hash: str, now: float) -> int | None:
        with session_scope() as s:
            row = s.get(PasswordLinkRow, token_hash)
            if row is None or row.expires_at < now:
                return None
            return row.user_id
