import re
from contextlib import contextmanager
lazy from collections.abc import Iterator

from sqlalchemy import event

from ofmhelpers.db.session import get_engine


class Statements:
    def __init__(self) -> None:
        self.run: list[str] = []

    def reading(self, table: str) -> list[str]:
        return [s for s in self.run if re.search(rf"\b(FROM|JOIN)\s+{table}\b", s)]

    def writing(self, table: str) -> list[str]:
        pattern = rf"^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+{table}\b"
        return [s for s in self.run if re.search(pattern, s)]


@contextmanager
def counting() -> Iterator[Statements]:
    seen = Statements()

    def note(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        seen.run.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", note)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", note)
