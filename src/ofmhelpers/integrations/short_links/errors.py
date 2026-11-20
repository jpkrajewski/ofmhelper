lazy from ofmhelpers.db.short_links.catalog.schema import RemoteLink


class LinkError(Exception): ...


class AuthError(LinkError): ...


class SlugTakenError(LinkError): ...


class QuotaError(LinkError): ...


class RateLimitedError(LinkError): ...


class UngatedError(LinkError):
    def __init__(self, message: str, remote: RemoteLink) -> None:
        super().__init__(message)
        self.remote = remote
