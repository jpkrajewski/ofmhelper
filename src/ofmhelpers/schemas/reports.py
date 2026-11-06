"""A report's run as stored (features/reports): when, and the counters it saw."""

from __future__ import annotations

from datetime import date  # noqa: TC003 -- pydantic reads it at runtime

from pydantic import BaseModel, Field


class DayCount(BaseModel):
    """One UTC day of one link as a run saw it."""

    views: int = 0
    clicks: int = 0


class ReportMarks(BaseModel):
    """What a run saw: each link's UTC days (from the day before it ran)."""

    links: dict[int, dict[date, DayCount]] = Field(default_factory=dict)


class ReportRun(BaseModel):
    name: str
    day: date
    ran_at: float
    sent: bool
    marks: ReportMarks
