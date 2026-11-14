"""People who sign in (`users`), the Google/Apple accounts linked to them
(`user_identities`), their set-password links (`password_links`) and their
own provider keys (`user_api_keys`). Reads of a user are cached; anything
returning a secret (the Google refresh token, an API key, a password hash)
never is: those must not reach Redis."""

import hashlib
import time
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from ofmhelpers.db.models import (
    PasswordLinkRow,
    UserApiKeyRow,
    UserIdentityRow,
    UserRow,
)
from ofmhelpers.db.repositories.cached_repository import (
    CachedRepository,
    cached,
    invalidates_cache,
)
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.users import LinkedAccount, Role, SavedKey, SavedKeys, User


def _credential_stamp(password_hash: str) -> str:
    """Changes with the password; "" without one. A truncated digest of
    the (salted) hash: safe in the cache and in the session cookie."""
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
        created_at=row.created_at,
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
        linked_at=row.linked_at,
    )


class OwnsDataError(ValueError):
    """A user who still owns jobs, phones, accounts or watched profiles:
    not deleted (their data would be orphaned); deactivate them instead."""


class UserRepository(CachedRepository):
    cache_namespace = "user"

    @cached
    def get(self, user_id: int) -> User | None:
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            return _user(row) if row else None

    @cached
    def by_email(self, email: str) -> User | None:
        with session_scope() as s:
            row = s.execute(
                select(UserRow).where(UserRow.email == email)
            ).scalar_one_or_none()
            return _user(row) if row else None

    @cached
    def list_all(self) -> list[User]:
        """Admins first, then by email."""
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
        """None when the email is taken -- also by a request racing this
        one (one INSERT ... ON CONFLICT DO NOTHING: never an IntegrityError)."""
        with session_scope() as s:
            row = s.scalars(
                insert(UserRow)
                .values(
                    email=email,
                    name=name,
                    role=role,
                    active=True,
                    created_at=time.time(),
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
        """Sets `fields` (role, active, name); None when there is no such user."""
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
        """Logout: every session of theirs signed in before now is out."""
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            if row is not None:
                row.sessions_ended_at = datetime.now(tz=UTC)

    @invalidates_cache
    def seen(self, user_id: int, at: float) -> None:
        """Their last action was at `at` (epoch seconds)."""
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            if row is not None:
                row.last_seen_at = datetime.fromtimestamp(at, tz=UTC)

    @invalidates_cache
    def end_idle_sessions(self, user_id: int) -> bool:
        """Ends their sessions unless they were already ended since their
        last action; whether this call was the one that did. One statement:
        of two callers at the same moment (the idle sweep, a sign-in) only
        one gets True."""
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
        """Active VAs whose last action is older than `before` (epoch
        seconds) and who have not been logged out since it: still signed in
        as far as anyone recorded. Not cached: the idle sweep's own read."""
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
        """Stamps a sign-in; keeps a newer refresh token (Google sends one
        only when it asked for consent)."""
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
        """Raises `OwnsDataError` while anything is theirs (RESTRICT)."""
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
        """Decrypted: never cached."""
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            return row.google_refresh_token if row else ""

    def api_keys(self, user_id: int) -> SavedKeys:
        """Each provider's key, decrypted: never cached."""
        with session_scope() as s:
            rows = s.execute(
                select(UserApiKeyRow).where(UserApiKeyRow.user_id == user_id)
            ).scalars()
            return SavedKeys(
                keys=[SavedKey(provider=r.provider, api_key=r.api_key) for r in rows]
            )

    def set_api_key(self, user_id: int, provider: str, api_key: str) -> None:
        """Stores the key; "" removes it."""
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
                        updated_at=time.time(),
                    )
                )

    # --- linked Google / Apple accounts ----------------------------------

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
        """False when that account is already linked to someone else.
        Insert first (ON CONFLICT DO NOTHING), then read who has it: two
        concurrent first sign-ins never collide on the primary key."""
        with session_scope() as s:
            s.execute(
                insert(UserIdentityRow)
                .values(
                    provider=provider,
                    subject=subject,
                    user_id=user_id,
                    email=email,
                    linked_at=time.time(),
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

    # --- passwords ---------------------------------------------------------

    def password_hash(self, user_id: int) -> str:
        """Never cached."""
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            return row.password_hash if row else ""

    @invalidates_cache
    def set_password_hash(self, user_id: int, password_hash: str) -> None:
        """Also voids every set-password link of the user."""
        with session_scope() as s:
            row = s.get(UserRow, user_id)
            if row is None:
                return
            row.password_hash = password_hash
            s.execute(delete(PasswordLinkRow).where(PasswordLinkRow.user_id == user_id))

    def add_password_link(
        self, user_id: int, token_hash: str, expires_at: float
    ) -> None:
        """The user's only link: an older one stops working."""
        with session_scope() as s:
            s.execute(delete(PasswordLinkRow).where(PasswordLinkRow.user_id == user_id))
            s.add(
                PasswordLinkRow(
                    token_hash=token_hash, user_id=user_id, expires_at=expires_at
                )
            )

    def link_user(self, token_hash: str, now: float) -> int | None:
        """Whose unexpired link this is; None when unknown or expired."""
        with session_scope() as s:
            row = s.get(PasswordLinkRow, token_hash)
            if row is None or row.expires_at < now:
                return None
            return row.user_id
