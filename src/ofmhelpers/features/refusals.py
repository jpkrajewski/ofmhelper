from contextlib import contextmanager
lazy from collections.abc import Iterator

lazy from pydantic import BaseModel

from ofmhelpers.db.devices.repository import DuplicateError
from ofmhelpers.integrations.ai_providers.errors import InvalidInputError
from ofmhelpers.integrations.downloaders.urls import NotALinkError


class RefusedError(ValueError):
    def __init__(self, message: str, detail: BaseModel | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class ForbiddenError(PermissionError): ...


class NotFoundError(LookupError): ...


class ConflictError(ValueError): ...


class TooSoonError(RuntimeError): ...


class AssetRefusedError(ValueError): ...


class CloneRefusedError(Exception): ...


class EditRefusedError(Exception): ...


class UnavailableError(RuntimeError): ...


class TryAgainError(RuntimeError): ...


@contextmanager
def duplicate_refused() -> Iterator[None]:
    try:
        yield
    except DuplicateError as exc:
        raise ConflictError(str(exc)) from exc


@contextmanager
def input_refused() -> Iterator[None]:
    try:
        yield
    except (InvalidInputError, NotALinkError) as exc:
        raise RefusedError(str(exc)) from exc
