"""Each report's runs: the last one, and claiming today's (once a day)."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from ofmhelpers.db.repositories import ReportRunRepository
from ofmhelpers.schemas.reports import ReportMarks, ReportRun

if TYPE_CHECKING:
    from datetime import date


@lru_cache(maxsize=1)
def _repository() -> ReportRunRepository:
    return ReportRunRepository()


def last_run(name: str) -> ReportRun | None:
    found = _repository().last(name)
    return ReportRun.model_validate(found) if found else None


def claim(name: str, day: date, ran_at: float, sent: bool, marks: ReportMarks) -> bool:
    """`name`'s run for `day`, unless it ran that day already."""
    return _repository().claim(name, day, ran_at, sent, marks.model_dump(mode="json"))
