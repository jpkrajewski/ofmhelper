"""What can go wrong asking a language model, as types rather than strings.

Every provider maps its own transport and API failures onto these, so a
caller decides "try again later", "fix the setup" or "keep going without an
answer" from the class alone -- never by parsing a provider's text. The
message is short and readable (it may reach a page); the provider's own
wording is kept in `detail` and as an exception note for the logs.
"""


class LLMError(Exception):
    """A provider refused or failed a request. `retryable`: the same call
    may succeed later unchanged."""

    retryable = False

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail
        if detail:
            # Shown under the traceback wherever the error is logged.
            self.add_note(detail)


class LLMConfigError(LLMError):
    """The app is not set up for this provider: no key configured, an
    unknown provider or model name."""


class LLMAuthError(LLMError):
    """The API key was rejected; every provider says it the same way."""

    def __init__(self, provider: str, detail: str = "") -> None:
        super().__init__(f"{provider}: wrong API key", detail)


class LLMInputError(LLMError):
    """The provider rejected the request itself: a media type it does not
    read, a file too big, a malformed request."""


class LLMRefusedError(LLMError):
    """The provider's safety filters blocked the prompt or the answer."""


class LLMOutputError(LLMError):
    """An answer came back but not in the asked-for shape."""


class LLMUnavailableError(LLMError):
    """Rate limited, overloaded, a 5xx or the network: worth trying again.
    `retry_after_s` is the provider's own hint, when it gave one."""

    retryable = True

    def __init__(
        self, message: str, detail: str = "", retry_after_s: float | None = None
    ) -> None:
        super().__init__(message, detail)
        self.retry_after_s = retry_after_s
