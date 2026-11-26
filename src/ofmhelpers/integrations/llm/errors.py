# Copyright (C) 2026 jpkrajewski


class LLMError(Exception):
    retryable = False

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail
        if detail:
            self.add_note(detail)


class LLMConfigError(LLMError): ...


class LLMAuthError(LLMError):
    def __init__(self, provider: str, detail: str = "") -> None:
        super().__init__(f"{provider}: wrong API key", detail)


class LLMInputError(LLMError): ...


class LLMRefusedError(LLMError): ...


class LLMOutputError(LLMError): ...


class LLMUnavailableError(LLMError):
    retryable = True

    def __init__(
        self,
        message: str,
        detail: str = "",
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message, detail)
        self.retry_after_s = retry_after_s


class LLMQuotaError(LLMUnavailableError): ...
