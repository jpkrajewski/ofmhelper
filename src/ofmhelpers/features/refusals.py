lazy from pydantic import BaseModel


class RefusedError(ValueError):
    def __init__(self, message: str, detail: BaseModel | None = None) -> None:
        super().__init__(message)
        self.detail = detail


class ForbiddenError(PermissionError): ...


class NotFoundError(LookupError): ...


class ConflictError(ValueError): ...


class TooSoonError(RuntimeError): ...


class UnavailableError(RuntimeError): ...


class TryAgainError(RuntimeError): ...
