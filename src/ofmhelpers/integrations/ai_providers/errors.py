# Copyright (C) 2026 jpkrajewski


class InvalidInputError(ValueError): ...


class ProviderError(Exception):
    retryable = False


class ProviderAuthError(ProviderError):
    def __init__(self, provider: str, detail: str = "") -> None:
        super().__init__(f"{provider}: wrong API key")
        self.detail = detail
        if detail:
            self.add_note(detail)


class ProviderInputError(ProviderError): ...


class ProviderNoCreditsError(ProviderInputError):
    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.provider = provider


class ProviderGenerationError(ProviderError): ...


class ProviderUnavailableError(ProviderError):
    retryable = True

    def __init__(self, message: str, *, maybe_made: bool = True) -> None:
        super().__init__(message)
        self.maybe_made = maybe_made


class ProviderRateLimitError(ProviderUnavailableError):
    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ProviderTimeoutError(ProviderError):
    retryable = True
