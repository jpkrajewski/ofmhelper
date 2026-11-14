"""/clone's SOP examples (`clone_examples`). Nothing cached: a handful of
rows, read when the dialog opens."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ofmhelpers.db.models import CloneExampleRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.clone_examples import CloneExample


def _example(row: CloneExampleRow) -> CloneExample:
    return CloneExample(
        id=row.id,
        title=row.title,
        prompt=row.prompt,
        position=row.position,
        job_id=row.job_id,
    )


class CloneExampleRepository:
    def every(self) -> list[CloneExample]:
        """In the order the page shows them."""
        with session_scope() as s:
            rows = s.execute(
                select(CloneExampleRow).order_by(
                    CloneExampleRow.position, CloneExampleRow.id
                )
            ).scalars()
            return [_example(r) for r in rows]

    def get(self, example_id: int) -> CloneExample | None:
        with session_scope() as s:
            row = s.get(CloneExampleRow, example_id)
            return _example(row) if row is not None else None

    def add(self, job_id: str, title: str, prompt: str) -> CloneExample:
        """The run as an example, last in the list; the same run added again
        is the one already there, under the new title."""
        last = select(CloneExampleRow.position).order_by(
            CloneExampleRow.position.desc()
        )
        with session_scope() as s:
            position = (s.execute(last.limit(1)).scalar_one_or_none() or 0) + 1
            row = s.execute(
                insert(CloneExampleRow)
                .values(job_id=job_id, title=title, prompt=prompt, position=position)
                .on_conflict_do_update(
                    constraint="uq_clone_examples_job_id",
                    set_={"title": title, "prompt": prompt},
                )
                .returning(CloneExampleRow)
            ).scalar_one()
            return _example(row)

    def remove(self, example_id: int) -> bool:
        with session_scope() as s:
            gone = s.execute(
                delete(CloneExampleRow)
                .where(CloneExampleRow.id == example_id)
                .returning(CloneExampleRow.id)
            )
            return bool(gone.all())
