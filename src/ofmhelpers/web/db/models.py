"""
SQLAlchemy ORM tables backing web/stores/jobs.py,
web/stores/applications.py, web/stores/watched_profiles.py and
web/stores/tracked_accounts.py, web/stores/devices.py and web/stores/drive_folders.py. Column sets mirror the exact dict/JSON shapes those
modules write today (see web/schemas.py for the Pydantic mirror), so the
step-5 backfill is a straight field-for-field copy.

`created_at` stays a float epoch (double precision) rather than a timestamp
-- the app writes `time.time()` everywhere and sorts on it, so keeping the
raw type is the least-invasive, behaviour-preserving choice.

File references are NOT a separate table: a job's result files live inside
its `result` JSONB payload exactly as they do in uploads/jobs.json.

The todo / model-roster / Instagram-stats / competitor / approval-token /
Drive day-folder tables still exist in Postgres with their data, but are no
longer mapped:
those features were removed and the data kept on purpose. alembic/env.py
lists them in RETIRED_TABLES so autogenerate never proposes dropping them.
"""

from __future__ import annotations

from datetime import date  # noqa: TC003 -- SQLAlchemy reads Mapped[] at runtime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ofmhelpers.web.db.encrypted import EncryptedText


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    """Someone who may sign in (ofmhelpers.signin): with Google or Apple
    (`UserIdentityRow`), or their email + password (`password_hash`,
    scrypt; "" = none set). An admin adds them on /users (or their email is
    in `OFM_ADMIN_EMAILS`); the account "admin" also takes
    `APP_PASSWORD_ADMIN`. Everyone sees only what is theirs; an admin also
    manages people and the admin tools. The Google refresh token (`EncryptedText`) is what Drive
    uploads go up as."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    last_login_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_refresh_token: Mapped[str] = mapped_column(
        EncryptedText, nullable=False, default=""
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Their "ALL ACCOUNTS" folder in their own Drive (web/drive_folders.py
    # finds or makes it); NULL until Google Drive is connected and swept.
    drive_root_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # The account `APP_PASSWORD_ADMIN` opens and the agency's data belongs
    # to; the first `OFM_ADMIN_EMAILS` admin's Google sign-in takes it over.
    is_password_account: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


class UserIdentityRow(Base):
    """A Google or Apple account linked to a user, by the provider's stable
    id (`subject`): an Apple private-relay address or a changed Gmail still
    finds its user."""

    __tablename__ = "user_identities"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    linked_at: Mapped[float] = mapped_column(Float, nullable=False)


class PasswordLinkRow(Base):
    """A one-time link to set a password (made on /users for a new user, or
    to reset one). Only its SHA-256 is stored."""

    __tablename__ = "password_links"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


class UserApiKeyRow(Base):
    """A user's own key for a generation provider (kie.ai, Higgsfield...):
    what their forms pre-fill, so their runs bill their account."""

    __tablename__ = "user_api_keys"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    api_key: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


class UserCookiesRow(Base):
    """A user's own cookies.txt (alembic b1c2d3e4f5a7): the logins their
    downloads and their accounts' scans send. Never shared with another
    user, except the shared watched-profile scan (web/stores/cookies.py)."""

    __tablename__ = "user_cookies"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    cookies: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    uploaded_at: Mapped[float] = mapped_column(Float, nullable=False)


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    # Set when the job reaches done/failed, cleared when it is run again.
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # result/preview are free-form per task type -- see module docstring.
    result: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # When its owner hid its card from the galleries; NULL = shown. A view
    # choice only: the job and its files stay.
    hidden_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        # Every read (list_jobs, pruning) sorts/filters by created_at.
        Index("ix_jobs_created_at", "created_at"),
    )


class ApplicationRow(Base):
    """One submission of the public `/` landing-page application form
    (templates/reachmodel.html). Write-once from the outside world, read-only
    in the CRM page -- there is nothing to update, so no status column."""

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    # Two optional columns, but the form requires at least one of them -- see
    # routers/apply.ApplicationForm: a lead we cannot message back is useless.
    phone: Mapped[str] = mapped_column(Text, nullable=False, default="")
    telegram_handle: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instagram_handle: Mapped[str] = mapped_column(Text, nullable=False, default="")
    monthly_revenue: Mapped[str] = mapped_column(Text, nullable=False, default="")
    experience_level: Mapped[str] = mapped_column(Text, nullable=False, default="")
    goals: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("ix_applications_created_at", "created_at"),)


class WatchedProfileRow(Base):
    """An Instagram profile /reels keeps rescanning (web/reel_watch.py), with
    its latest scan: `result` is an `instagram.ProfileReels` dump, `error`
    the last scan's failure. `claimed_at` marks a scan in flight, so two
    sweepers (one per API process) never queue the same profile twice."""

    __tablename__ = "watched_profiles"

    # Whose watch list (alembic d3e4f5a6b7c8). A username two owners watch
    # is two rows, scanned once: a scan writes every row of its username.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    username: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    # Free-form label the feed filters by ("asian", "gay", ...); None = none.
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added_at: Mapped[float] = mapped_column(Float, nullable=False)
    scanned_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_watched_profiles_group_name", "group_name"),)


class ProfileGroupRow(Base):
    """A /reels group created on its own ("+ New group"), so it is in every
    dropdown before any profile is in it. Groups in use also come from
    `watched_profiles.group_name`; this table only adds the empty ones."""

    __tablename__ = "profile_groups"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class ProfileScanStatRow(Base):
    """One successful scan of a watched profile, as numbers: the history the
    feed's latest-scan-only `watched_profiles.result` does not keep (growth,
    posting rate, which groups perform). Written with every scan
    (`WatchedProfileRepository.record_scan`); not deleted with the profile,
    so stopping to watch an account keeps its past."""

    __tablename__ = "profile_scan_stats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scanned_at: Mapped[float] = mapped_column(Float, nullable=False)
    window_days: Mapped[float] = mapped_column(Float, nullable=False)
    followers: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reels: Mapped[int] = mapped_column(Integer, nullable=False)
    eligible: Mapped[int] = mapped_column(Integer, nullable=False)
    views_total: Mapped[int] = mapped_column(BigInteger, nullable=False)
    views_max: Mapped[int] = mapped_column(BigInteger, nullable=False)
    views_median: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_profile_scan_stats_username_scanned_at", "username", "scanned_at"),
    )


class CreatorRow(Base):
    """A creator /accounts assigns accounts to (any platform). Created the
    first time a name is assigned; the name is how the page refers to it."""

    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Whose it is (alembic d3e4f5a6b7c8): only they see it (admins too).
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_creators_owner_name"),
    )


class TrackedAccountRow(Base):
    """A social account /accounts tracks (web/account_tracker.py rescans it
    every `rescan_interval_s`), with its latest scan: `profile` is a
    `socials.AccountProfile` dump, `metrics` an `AccountMetrics` one (what
    the list shows); its posts are `TrackedPostRow`s. `covered_since`: the
    publish time from which those posts are complete (0: every post was
    read; None: no scan read any yet). `error` is the last scan's failure,
    shown next to the previous numbers; `claimed_at` marks a scan in
    flight."""

    __tablename__ = "tracked_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Whose it is (alembic d3e4f5a6b7c8): only they see it (admins too).
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # A `socials.platforms()` key.
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    handle: Mapped[str] = mapped_column(String(64), nullable=False)
    creator_id: Mapped[int | None] = mapped_column(
        ForeignKey("creators.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    added_at: Mapped[float] = mapped_column(Float, nullable=False)
    scanned_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Set by "Remove": no longer scanned or listed, every record kept;
    # adding the handle again clears it.
    untracked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The platform's stable id (`AccountProfile.account_id`), set by the
    # first successful scan: a renamed account's new handle merges into it.
    platform_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Consecutive failed scans; backs the rescan interval off (see
    # `AccountsSettings.max_rescan_interval_s`).
    failed_scans: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    covered_since: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "platform", "handle", name="uq_tracked_accounts_platform_handle"
        ),
        # NULLs never collide: only scanned accounts carry an id.
        UniqueConstraint(
            "user_id",
            "platform",
            "platform_account_id",
            name="uq_tracked_accounts_platform_account",
        ),
        Index("ix_tracked_accounts_creator_id", "creator_id"),
    )


class TrackedPostRow(Base):
    """A post of a tracked account, kept while published within
    `settings.accounts.post_retention_days`: scans upsert the posts they
    read (by the platform's own `post_id`) and never remove one they did
    not see; only age prunes. `views` are as of `last_seen_at`. Deleted
    with its account."""

    __tablename__ = "tracked_posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_accounts.id", ondelete="CASCADE"), nullable=False
    )
    post_id: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    thumbnail_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    views: Mapped[int] = mapped_column(BigInteger, nullable=False)
    posted_at: Mapped[float] = mapped_column(Float, nullable=False)
    first_seen_at: Mapped[float] = mapped_column(Float, nullable=False)
    last_seen_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("account_id", "post_id", name="uq_tracked_posts_account_post"),
        Index("ix_tracked_posts_account_id_posted_at", "account_id", "posted_at"),
    )


class AccountSnapshotRow(Base):
    """One successful scan of a tracked account: the history growth and the
    analytics chart are read from. `followers` is a column (growth is a SQL
    lookup); the rest is the scan's `AccountMetrics` dump, so a platform
    with other numbers needs no migration. Deleted with its account."""

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_accounts.id", ondelete="CASCADE"), nullable=False
    )
    taken_at: Mapped[float] = mapped_column(Float, nullable=False)
    followers: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_account_snapshots_account_id_taken_at", "account_id", "taken_at"),
    )


class DeviceRow(Base):
    """A physical phone of the agency (/phones). `model_id`/`color_id` are
    `ofmhelpers.phones.catalog()` ids (`custom_model` for a phone the
    catalog does not list). Its logins are linked through
    `DeviceLoginRow`. "Retire" sets `retired_at`: off the list and the
    export, every login kept."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Whose it is (alembic d3e4f5a6b7c8): only they see it (admins too).
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # How its owner calls it ("Phone 07"); unique per owner.
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    color_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    custom_model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    added_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    retired_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "label", name="uq_devices_owner_label"),
    )


class LoginRow(Base):
    """One account: an iCloud, a Gmail, an Instagram, a website... `service`
    is an `ofmhelpers.phones.services()` key; `provider` names it for a
    custom service ("Acme Corp"), "" otherwise. Stored once, signed in on
    any number of phones (`DeviceLoginRow`). Password and backup codes are
    encrypted at rest (`EncryptedText`); "" stays "", so a list can tell
    which logins have one without decrypting anything."""

    __tablename__ = "logins"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # The owner of the phones it is on: a login links only to its owner's
    # phones, so nobody reveals another's password by adding the same handle.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(EncryptedText, nullable=False, default="")
    backup_codes: Mapped[str] = mapped_column(EncryptedText, nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    added_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    # Set: the account was removed everywhere (/accounts, or its Drive folder
    # deleted by hand) -- hidden from every phone, password kept, restorable
    # (alembic e5f6a7b8c9d1). Its phone links stay for the restore.
    archived_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "service", "provider", "username", name="uq_logins_identity"
        ),
    )


class DeviceLoginRow(Base):
    """A login signed in on a phone. Deleted with either side; a login no
    phone links any more is deleted with its last link (repository)."""

    __tablename__ = "device_logins"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    login_id: Mapped[int] = mapped_column(
        ForeignKey("logins.id", ondelete="CASCADE"), primary_key=True
    )
    linked_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("ix_device_logins_login_id", "login_id"),)


class DriveAccountFolderRow(Base):
    """An Instagram login's folder in the shared Drive folder (/drive),
    "<handle> - <owner>". Kept by id, so a new owner renames it instead of
    starting another. Deleted with the login; the Drive folder never is."""

    __tablename__ = "drive_account_folders"

    login_id: Mapped[int] = mapped_column(
        ForeignKey("logins.id", ondelete="CASCADE"), primary_key=True
    )
    folder_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    # The root folder it was made or found in: a row of another root (dev
    # once pointed at the live folder) is ignored, never written into.
    root_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Its POSTED folder inside it; NULL until the sweep makes or finds one.
    posted_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Its last read (`schemas.AccountScan`), what /drive opens with before
    # the live read arrives; NULL until read once.
    scan: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    scanned_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class DriveUploadRow(Base):
    """A clone result uploaded (or on its way) into an account's Drive
    folder, as the uploader's Google account (web/drive_uploads.py).
    `status`: queued, done (`drive_file_id`, `drive_url`) or failed
    (`error`). One per file and folder: never uploaded twice there."""

    __tablename__ = "drive_uploads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_index: Mapped[int] = mapped_column(Integer, nullable=False)
    login_id: Mapped[int] = mapped_column(
        ForeignKey("logins.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    folder_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    drive_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    drive_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "job_id", "file_index", "login_id", name="uq_drive_uploads_file_folder"
        ),
    )


class DrivePostedFileRow(Base):
    """A file seen in a login's POSTED folder, from the sweep that first saw
    it: Drive keeps no time a file was moved, so this is what "posted today"
    counts -- only the files seen in the login's current account folder
    (`folder_id`), so a new folder (a dev copy's own root) starts at zero.
    Never deleted when the file leaves POSTED (the live count drops, the
    history stays); deleted with the login."""

    __tablename__ = "drive_posted_files"

    file_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    login_id: Mapped[int] = mapped_column(
        ForeignKey("logins.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_at: Mapped[float] = mapped_column(Float, nullable=False)
    # The account folder it was seen in (NULL: a login without one then).
    folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_drive_posted_files_login_seen", "login_id", "first_seen_at"),
    )


class ShortLinkRow(Base):
    """A link on a link provider (/links): one we made ("Generate links":
    one per Instagram account, `account_id`) or one the provider listed on
    a sync. `remote_id` is the provider's id for it (NULL while still
    being made); `status`: creating | active | inactive | failed |
    deleting. `adult`: visitors confirm 18+ first (NULL: the provider does
    not say). A link the provider no longer lists is deleted here too."""

    __tablename__ = "short_links"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    remote_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    destination: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(String(32), nullable=True)
    creator_id: Mapped[int | None] = mapped_column(
        ForeignKey("creators.id", ondelete="SET NULL"), nullable=True
    )
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("tracked_accounts.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    adult: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "domain", "slug", name="uq_short_links_address"
        ),
        UniqueConstraint(
            "user_id", "provider", "remote_id", name="uq_short_links_remote"
        ),
        Index("ix_short_links_account_id", "account_id"),
    )


class ShortLinkDayRow(Base):
    """One UTC day of one link's numbers, as its provider reports them:
    landing views and clicks through. Rewritten by every sync."""

    __tablename__ = "short_link_days"

    link_id: Mapped[int] = mapped_column(
        ForeignKey("short_links.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    views: Mapped[int] = mapped_column(Integer, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False)


class LinkSyncRow(Base):
    """A user's account on a link provider, as the sweeper keeps it: when it
    was last synced (or claimed by a sync), the last sync's error, and the
    plan's quota then (`links.Usage`)."""

    __tablename__ = "link_syncs"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    synced_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class CloneAiCheckRow(Base):
    """One AI check (`ofmhelpers.clone_ai_check`, web/clone_ai_checks.py): is a
    generated video too glitchy to post? The verdict (GOOD/GLITCHY, None when
    the check itself failed -- `error`), how sure the model was, every glitch
    it saw, its reasoning, and what a debugger needs: the prompt, the raw
    answer, who answered, the stills' times (enough to draw the contact
    sheet again), attempts and timings. `job_id`/`file_index`: the result it
    checked, when it was one; `user_id`: whose."""

    __tablename__ = "clone_ai_checks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video: Mapped[str] = mapped_column(Text, nullable=False)
    video_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    stills_at: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    frame_px: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    glitch_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    glitches: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_system: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_answer: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_s: Mapped[float] = mapped_column(Float, nullable=False)
    llm_s: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class CloneFromLinkRow(Base):
    """A /clone-links result on its way to Drive (web/clone_links/review.py):
    checked for glitches (`check_id`: its `clone_ai_checks` row), then uploaded
    as its owner's Google account into their root's GOOD REELS or TRIAL
    REELS (`folder_name`). `status`: checking, uploading, done
    (`drive_file_id`, `drive_url`) or failed (`error`). One per result
    file."""

    __tablename__ = "clone_from_links"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    file_index: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    check_id: Mapped[int | None] = mapped_column(
        ForeignKey("clone_ai_checks.id", ondelete="SET NULL"), nullable=True
    )
    folder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    drive_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("job_id", "file_index", name="uq_clone_from_links_file"),
    )
