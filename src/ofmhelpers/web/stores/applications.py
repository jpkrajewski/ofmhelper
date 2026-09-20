"""
ofmhelpers/web/stores/applications.py

What the public landing page's application form (templates/reachmodel.html)
collected: one row per submission, written by routers/apply.py and read by the
admin CRM page (routers/admin/applications.py).

Durable like todos, not like job history: a lost application is a lost lead.
"""

from __future__ import annotations

from functools import lru_cache

from ofmhelpers.web.db.repositories import ApplicationRepository


@lru_cache(maxsize=1)
def _repository() -> ApplicationRepository:
    """The process-wide application repository, built on first use rather than
    at import (constructing it binds a Redis connection for its cache)."""
    return ApplicationRepository()


def list_applications() -> list[dict]:
    """Newest first."""
    return _repository().list_all()


def add_application(fields: dict) -> dict:
    """`fields` is an already-validated submission -- see
    routers/apply.ApplicationForm."""
    return _repository().add(fields)


def delete_application(application_id: str) -> bool:
    """Returns False if no such application exists."""
    return _repository().delete(application_id)
