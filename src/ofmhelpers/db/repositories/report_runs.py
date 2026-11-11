"""Each report's runs (`report_runs`), one per day: the claim that it went out, when,
and the counters it saw. Not cached: read once a minute by one sweep."""

from typing import Any
lazy from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.models import ReportRunRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.reports import ReportMarks, ReportRun


class ReportRunRepository:
    def last(self, name: str) -> ReportRun | None:
        """The newest run of `name`."""
        with session_scope() as s:
            row = s.execute(
                select(ReportRunRow)
                .where(ReportRunRow.name == name)
                .order_by(ReportRunRow.day.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                return None
            return ReportRun(
                name=row.name,
                day=row.day,
                ran_at=row.ran_at,
                sent=row.sent,
                marks=ReportMarks.model_validate(row.marks),
            )

    def claim(
        self, name: str, day: date, ran_at: float, sent: bool, marks: dict[str, Any]
    ) -> bool:
        """The run of `name` for `day`; False when there is one already."""
        with session_scope() as s:
            made = s.execute(
                insert(ReportRunRow)
                .values(name=name, day=day, ran_at=ran_at, sent=sent, marks=marks)
                .on_conflict_do_nothing()
                .returning(ReportRunRow.name)
            ).scalar_one_or_none()
            return made is not None
