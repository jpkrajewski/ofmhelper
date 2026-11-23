# Copyright (C) 2026 jpkrajewski

from datetime import date

from pydantic import BaseModel, Field


class DayCount(BaseModel):
    views: int = 0
    clicks: int = 0


class ReportMarks(BaseModel):
    links: dict[int, dict[date, DayCount]] = Field(default_factory=dict)


class ReportRun(BaseModel):
    name: str
    day: date
    ran_at: float
    sent: bool
    marks: ReportMarks
