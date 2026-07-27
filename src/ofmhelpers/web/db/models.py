"""
SQLAlchemy ORM tables backing web/jobs.py, web/todos.py and
web/approval_tokens.py. Column sets mirror the exact dict/JSON shapes those
modules write today (see web/schemas.py for the Pydantic mirror), so the
step-5 backfill is a straight field-for-field copy.

`created_at` stays a float epoch (double precision) rather than a timestamp
-- the app writes `time.time()` everywhere and sorts on it, so keeping the
raw type is the least-invasive, behaviour-preserving choice.

File references are NOT a separate table: a job's result files live inside
its `result` JSONB payload exactly as they do in uploads/jobs.json.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # result/preview are free-form per task type -- see module docstring.
    result: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        # Every read (list_jobs, pruning) sorts/filters by created_at.
        Index("ix_jobs_created_at", "created_at"),
    )


class TodoRow(Base):
    __tablename__ = "todos"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    comments: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    asset_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reject_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    drive_uploaded_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    drive_upload_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_todos_created_at", "created_at"),)


class ModelRow(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    profile_picture_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_picture_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    onlyfans_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    instagram_accounts: Mapped[list[InstagramAccountRow]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="InstagramAccountRow.created_at",
    )
    contacts: Mapped[list[ModelContactRow]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="ModelContactRow.created_at",
    )

    __table_args__ = (Index("ix_models_created_at", "created_at"),)


class InstagramAccountRow(Base):
    """The account itself plus the credentials/SIM the team manages it with --
    all optional, filled in on the model's edit page."""

    __tablename__ = "instagram_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("models.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    sim_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    password: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    model: Mapped[ModelRow] = relationship(back_populates="instagram_accounts")

    __table_args__ = (Index("ix_instagram_accounts_model_id", "model_id"),)


class ModelContactRow(Base):
    """One free-form contact channel for a model, e.g. type="WhatsApp",
    value="+4534343434". Many per model."""

    __tablename__ = "model_contacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("models.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    model: Mapped[ModelRow] = relationship(back_populates="contacts")

    __table_args__ = (Index("ix_model_contacts_model_id", "model_id"),)


class InstagramStatsRow(Base):
    """Latest scrape result for one instagram_accounts row (upserted in
    place, not history -- see InstagramStatsRepository). `posts` holds the
    last N posts as a JSON list of {"url", "views", "likes", "shares"}."""

    __tablename__ = "instagram_stats"

    account_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("instagram_accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    posts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    checked_at: Mapped[float] = mapped_column(Float, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ApprovalTokenRow(Base):
    __tablename__ = "approval_tokens"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    todo_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    used_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        # _save() purges expired tokens on every write -- filter by expires_at.
        Index("ix_approval_tokens_expires_at", "expires_at"),
    )
