from ofmhelpers.errors import AppError
lazy from ofmhelpers.schemas.link_catalog.models import RemoteLink


class LinkError(AppError): ...


class AuthError(LinkError): ...


class SlugTakenError(LinkError): ...


class QuotaError(LinkError): ...


class RateLimitedError(LinkError): ...


class UngatedError(LinkError):
    def __init__(self, message: str, remote: RemoteLink) -> None:
        super().__init__(message)
        self.remote = remote
