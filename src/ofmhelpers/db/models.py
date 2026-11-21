from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, column_property, mapped_column

from ofmhelpers.db.encrypted import EncryptedText


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Authored:
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class UserRow(Timestamps, Authored, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    google_refresh_token: Mapped[str] = mapped_column(
        EncryptedText, nullable=False, default="", deferred=True
    )
    drive_connected: Mapped[bool] = column_property(
        func.length(google_refresh_token) > 0
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")
    drive_root_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_password_account: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    sessions_ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserIdentityRow(Timestamps, Authored, Base):
    __tablename__ = "user_identities"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, default="")


class PasswordLinkRow(Base):
    __tablename__ = "password_links"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)


class UserApiKeyRow(Timestamps, Authored, Base):
    __tablename__ = "user_api_keys"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    api_key: Mapped[str] = mapped_column(EncryptedText, nullable=False)


class UserCookiesRow(Timestamps, Authored, Base):
    __tablename__ = "user_cookies"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    cookies: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")


class ChatThreadRow(Base):
    __tablename__ = "chat_threads"

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    model_username: Mapped[str] = mapped_column(String(255), nullable=False)
    last_message_id: Mapped[str] = mapped_column(String(64), nullable=False)


class ChatFanMessageRow(Base):
    __tablename__ = "chat_fan_messages"
    __table_args__ = (
        Index(
            "ix_chat_fan_messages_model_sent", "platform", "model_username", "sent_at"
        ),
        Index("ix_chat_fan_messages_channel", "platform", "channel_id"),
        Index(
            "ix_chat_fan_messages_open",
            "platform",
            "model_username",
            "channel_id",
            "sent_at",
            postgresql_where=text("replied_at IS NULL"),
        ),
    )

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(255), nullable=False)
    model_username: Mapped[str] = mapped_column(String(255), nullable=False)
    fan_name: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[float] = mapped_column(Float, nullable=False)
    replied_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class ChatModelMessageRow(Base):
    __tablename__ = "chat_model_messages"
    __table_args__ = (
        Index(
            "ix_chat_model_messages_channel_sent", "platform", "channel_id", "sent_at"
        ),
        Index("ix_chat_model_messages_sent", "sent_at"),
    )

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sent_at: Mapped[float] = mapped_column(Float, nullable=False)


class ChatAccountRow(Base):
    __tablename__ = "chat_accounts"

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    is_agency: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    needs_reconnect: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    access_token: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    refresh_token: Mapped[str] = mapped_column(EncryptedText, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    connected_from: Mapped[str] = mapped_column(
        String(2048), nullable=False, default="", server_default=""
    )


class ChatNotificationRow(Base):
    __tablename__ = "chat_notifications"
    __table_args__ = (Index("ix_chat_notifications_at", "at"),)

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    model_username: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    detail: Mapped[str] = mapped_column(String(64), nullable=False)
    at: Mapped[float] = mapped_column(Float, nullable=False)


class ChatSaleRow(Base):
    __tablename__ = "chat_sales"
    __table_args__ = (Index("ix_chat_sales_at", "at"),)

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    model_username: Mapped[str] = mapped_column(String(255), nullable=False)
    gross_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    at: Mapped[float] = mapped_column(Float, nullable=False)


class ReportRunRow(Base):
    __tablename__ = "report_runs"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    ran_at: Mapped[float] = mapped_column(Float, nullable=False)
    sent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    marks: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class ChatWebhookRow(Base):
    __tablename__ = "chat_webhooks"

    platform: Mapped[str] = mapped_column(String(32), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    model_username: Mapped[str] = mapped_column(String(255), nullable=False)
    webhook_id: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(EncryptedText, nullable=False)


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    hidden_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)
    cost_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    __table_args__ = (
        Index("ix_jobs_created_at", "created_at"),
        Index("ix_jobs_user_id_created_at", "user_id", "created_at"),
        Index(
            "ix_jobs_running_user_id",
            "user_id",
            postgresql_where=text("status = 'running'"),
        ),
    )


class DownloadRow(Base):
    __tablename__ = "downloads"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    asset_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    stored: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("job_id", "position", name="uq_downloads_job_position"),
        Index("ix_downloads_user_id_url", "user_id", "url"),
        Index("ix_downloads_stored_last_used_at", "stored", "last_used_at"),
    )


class ApplicationRow(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(Text, nullable=False, default="")
    telegram_handle: Mapped[str] = mapped_column(Text, nullable=False, default="")
    instagram_handle: Mapped[str] = mapped_column(Text, nullable=False, default="")
    monthly_revenue: Mapped[str] = mapped_column(Text, nullable=False, default="")
    experience_level: Mapped[str] = mapped_column(Text, nullable=False, default="")
    goals: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("ix_applications_created_at", "created_at"),)


class WatchedProfileRow(Base):
    __tablename__ = "watched_profiles"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    username: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    added_at: Mapped[float] = mapped_column(Float, nullable=False)
    scanned_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_watched_profiles_group_name", "group_name"),)


class ProfileGroupRow(Timestamps, Authored, Base):
    __tablename__ = "watched_profile_groups"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    name: Mapped[str] = mapped_column(String(64), primary_key=True)


class ProfileScanStatRow(Base):
    __tablename__ = "watched_profile_scans"

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


class CreatorRow(Timestamps, Authored, Base):
    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_creators_owner_name"),
    )


class TrackedAccountRow(Timestamps, Authored, Base):
    __tablename__ = "tracked_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    handle: Mapped[str] = mapped_column(String(64), nullable=False)
    creator_id: Mapped[int | None] = mapped_column(
        ForeignKey("creators.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    scanned_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    untracked_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    platform_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
        UniqueConstraint(
            "user_id",
            "platform",
            "platform_account_id",
            name="uq_tracked_accounts_platform_account",
        ),
        Index("ix_tracked_accounts_creator_id", "creator_id"),
    )


class TrackedPostRow(Base):
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
    __tablename__ = "tracked_account_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_accounts.id", ondelete="CASCADE"), nullable=False
    )
    taken_at: Mapped[float] = mapped_column(Float, nullable=False)
    followers: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_account_snapshots_account_id_taken_at", "account_id", "taken_at"),
    )


class DeviceRow(Timestamps, Authored, Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    color_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    custom_model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    owner: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    retired_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "label", name="uq_devices_owner_label"),
    )


class LoginRow(Timestamps, Authored, Base):
    __tablename__ = "logins"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password: Mapped[str] = mapped_column(
        EncryptedText,
        nullable=False,
        default="",
        deferred=True,
        deferred_group="secret",
    )
    backup_codes: Mapped[str] = mapped_column(
        EncryptedText,
        nullable=False,
        default="",
        deferred=True,
        deferred_group="secret",
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    archived_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "service", "provider", "username", name="uq_logins_identity"
        ),
    )


class DeviceLoginRow(Timestamps, Authored, Base):
    __tablename__ = "device_logins"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    login_id: Mapped[int] = mapped_column(
        ForeignKey("logins.id", ondelete="CASCADE"), primary_key=True
    )

    __table_args__ = (Index("ix_device_logins_login_id", "login_id"),)


class DriveAccountFolderRow(Base):
    __tablename__ = "drive_account_folders"

    login_id: Mapped[int] = mapped_column(
        ForeignKey("logins.id", ondelete="CASCADE"), primary_key=True
    )
    folder_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    root_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    posted_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scan: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    scanned_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class DriveVaFolderRow(Base):
    __tablename__ = "drive_va_folders"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    folder_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    root_folder_id: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DriveUploadRow(Base):
    __tablename__ = "drive_uploads"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_index: Mapped[int] = mapped_column(Integer, nullable=False)
    login_id: Mapped[int | None] = mapped_column(
        ForeignKey("logins.id", ondelete="CASCADE"), nullable=True
    )
    va_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
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
        UniqueConstraint(
            "job_id", "file_index", "va_id", name="uq_drive_uploads_file_va"
        ),
        CheckConstraint(
            "(login_id IS NULL) <> (va_id IS NULL)", name="ck_drive_uploads_one_folder"
        ),
    )


class IdempotencyKeyRow(Base):
    __tablename__ = "idempotency_keys"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('started', 'done')", name="ck_idempotency_keys_status"
        ),
        CheckConstraint("length(key) <= 255", name="ck_idempotency_keys_key_length"),
        Index("ix_idempotency_keys_created_at", "created_at"),
    )


class DrivePostedFileRow(Base):
    __tablename__ = "drive_posted_files"

    file_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    login_id: Mapped[int] = mapped_column(
        ForeignKey("logins.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_at: Mapped[float] = mapped_column(Float, nullable=False)
    folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_drive_posted_files_login_seen", "login_id", "first_seen_at"),
    )


class ShortLinkRow(Timestamps, Authored, Base):
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

    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "domain", "slug", name="uq_short_links_address"
        ),
        UniqueConstraint(
            "user_id", "provider", "remote_id", name="uq_short_links_remote"
        ),
        Index("ix_short_links_account_id", "account_id"),
        Index(
            "ix_short_links_bridge",
            "domain",
            "slug",
            postgresql_where=text("provider = 'self' AND status = 'active'"),
        ),
        Index(
            "uq_short_links_self_address",
            "domain",
            "slug",
            unique=True,
            postgresql_where=text(
                "provider = 'self' AND status IN ('creating', 'active', 'inactive')"
            ),
        ),
    )


class ShortLinkDayRow(Base):
    __tablename__ = "short_link_daily_stats"

    link_id: Mapped[int] = mapped_column(
        ForeignKey("short_links.id", ondelete="CASCADE"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    views: Mapped[int] = mapped_column(Integer, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False)


class LinkSyncRow(Base):
    __tablename__ = "short_link_syncs"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    synced_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class SelfLinkDomainRow(Timestamps, Authored, Base):
    __tablename__ = "self_link_domains"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    host: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class SelfLinkClickRow(Base):
    __tablename__ = "self_link_clicks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    link_id: Mapped[int] = mapped_column(
        ForeignKey("short_links.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tz: Mapped[str | None] = mapped_column(String(64), nullable=True)
    referrer: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
    device: Mapped[str | None] = mapped_column(String(16), nullable=True)
    browser: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ua: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_self_link_clicks_link_ts", "link_id", "ts"),)


class CloneAiCheckRow(Base):
    __tablename__ = "clone_ai_checks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    error_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thinking_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class CloneFromLinkRow(Base):
    __tablename__ = "clone_from_links"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
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
    path: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("job_id", "file_index", name="uq_clone_from_links_file"),
    )


class PicturePresetRow(Timestamps, Authored, Base):
    __tablename__ = "picture_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    images: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    aspect_ratio: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_picture_presets_owner_name"),
    )


class CloneExampleRow(Timestamps, Authored, Base):
    __tablename__ = "clone_examples"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    job_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (UniqueConstraint("job_id", name="uq_clone_examples_job_id"),)


class PhoneRevisionRow(Base):
    __tablename__ = "phone_revisions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    txid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    change: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "txid", "created_at", name="uq_phone_revisions_transaction"
        ),
        Index("ix_phone_revisions_user_id_id", "user_id", "id"),
    )


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    row_id: Mapped[str] = mapped_column(Text, nullable=False)
    op: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    viewing_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    via: Mapped[str] = mapped_column(Text, nullable=False)
    ref: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    trace_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    txid: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("txid_current()")
    )
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("op IN ('INSERT', 'UPDATE', 'DELETE')", name="ck_audit_log_op"),
        Index("ix_audit_log_table_name_row_id_id", "table_name", "row_id", "id"),
        Index("ix_audit_log_actor_id_id", "actor_id", "id"),
        Index("ix_audit_log_owner_id_id", "owner_id", "id"),
    )


class UserEventTargetRow(Timestamps, Authored, Base):
    __tablename__ = "user_event_targets"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    sender: Mapped[str] = mapped_column(Text, primary_key=True)
    target: Mapped[str] = mapped_column(Text, nullable=False)


class DistributionChannelRow(Timestamps, Authored, Base):
    __tablename__ = "distribution_channels"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(Text, nullable=False)
    account_name: Mapped[str] = mapped_column(Text, nullable=False)
    cookies: Mapped[str] = mapped_column(
        EncryptedText, nullable=False, default="", deferred=True
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resume_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cookies_expire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "platform",
            "account_name",
            name="uq_distribution_channels_account",
        ),
    )


class DistributionPresetRow(Timestamps, Authored, Base):
    __tablename__ = "distribution_presets"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    link_id: Mapped[int | None] = mapped_column(
        ForeignKey("short_links.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_distribution_presets_owner_key"),
    )


class DistributionPostRow(Base):
    __tablename__ = "distribution_posts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("distribution_channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    preset: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "channel_id", "file_id", name="uq_distribution_posts_channel_file"
        ),
        Index("ix_distribution_posts_channel_status", "channel_id", "status"),
        Index("ix_distribution_posts_file_status", "file_id", "status"),
    )
