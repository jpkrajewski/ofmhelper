from functools import lru_cache

from ofmhelpers.db.database.repository import DatabaseRepository


@lru_cache(maxsize=1)
def _repository() -> DatabaseRepository:
    return DatabaseRepository()


def database_bytes() -> int:
    return _repository().size_bytes()
