"""
ofmhelpers/web/stores/applications.py

What the public landing page's application form (templates/reachmodel.html)
collected: one row per submission, written by routers/apply.py and read by the
admin CRM page (routers/admin/applications.py).

Durable, unlike job history: a lost application is a lost lead.
"""

from functools import lru_cache

from ofmhelpers.db.repositories import ApplicationRepository
lazy from ofmhelpers.schemas import Application, ApplicationForm


@lru_cache(maxsize=1)
def _repository() -> ApplicationRepository:
    """The process-wide application repository, built on first use rather than
    at import (constructing it binds a Redis connection for its cache)."""
    return ApplicationRepository()


def list_applications() -> list[Application]:
    """Newest first."""
    return _repository().list_all()


def add_application(form: ApplicationForm) -> Application:
    return _repository().add(form.model_dump())


def delete_application(application_id: str) -> bool:
    """Returns False if no such application exists."""
    return _repository().delete(application_id)
