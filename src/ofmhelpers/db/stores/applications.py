from functools import lru_cache

from ofmhelpers.db.repositories import ApplicationRepository
lazy from ofmhelpers.schemas import Application, ApplicationForm


@lru_cache(maxsize=1)
def _repository() -> ApplicationRepository:
    return ApplicationRepository()


def list_applications() -> list[Application]:
    return _repository().list_all()


def add_application(form: ApplicationForm) -> Application:
    return _repository().add(form.model_dump())


def delete_application(application_id: str) -> bool:
    return _repository().delete(application_id)
