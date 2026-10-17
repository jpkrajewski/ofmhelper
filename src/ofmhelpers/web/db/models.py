"""
SQLAlchemy ORM tables backing web/stores/jobs.py,
web/stores/applications.py and web/stores/watched_profiles.py. Column sets mirror the exact dict/JSON shapes those
modules write today (see web/schemas.py for the Pydantic mirror), so the
step-5 backfill is a straight field-for-field copy.

`created_at` stays a float epoch (double precision) rather than a timestamp
-- the app writes `time.time()` everywhere and sorts on it, so keeping the
raw type is the least-invasive, behaviour-preserving choice.

File references are NOT a separate table: a job's result files live inside
its `result` JSONB payload exactly as they do in uploads/jobs.json.

The todo / model-roster / Instagram-stats / competitor / approval-token
tables still exist in Postgres with their data, but are no longer mapped:
those features were removed and the data kept on purpose. alembic/env.py
lists them in RETIRED_TABLES so autogenerate never proposes dropping them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
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
