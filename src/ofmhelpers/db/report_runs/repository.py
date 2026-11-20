from typing import Any
lazy from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.models import ReportRunRow
from ofmhelpers.db.report_runs.schema import ReportMarks, ReportRun
from ofmhelpers.db.session import session_scope


class ReportRunRepository:
    def last(self, name: str) -> ReportRun | None:
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
        with session_scope() as s:
            made = s.execute(
                insert(ReportRunRow)
                .values(name=name, day=day, ran_at=ran_at, sent=sent, marks=marks)
                .on_conflict_do_nothing()
                .returning(ReportRunRow.name)
            ).scalar_one_or_none()
            return made is not None
