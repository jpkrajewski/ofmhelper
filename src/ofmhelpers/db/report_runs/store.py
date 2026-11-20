from functools import lru_cache
lazy from datetime import date

from ofmhelpers.db.report_runs.repository import ReportRunRepository
lazy from ofmhelpers.db.report_runs.schema import ReportMarks, ReportRun


@lru_cache(maxsize=1)
def _repository() -> ReportRunRepository:
    return ReportRunRepository()


def last_run(name: str) -> ReportRun | None:
    return _repository().last(name)


def claim(name: str, day: date, ran_at: float, sent: bool, marks: ReportMarks) -> bool:
    return _repository().claim(name, day, ran_at, sent, marks.model_dump(mode="json"))
