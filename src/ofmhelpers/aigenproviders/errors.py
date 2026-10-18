"""What can go wrong talking to a provider, as types rather than strings.

Every adapter maps its own transport and API failures onto these, so the
runner and the web layer decide "retry", "keep for recovery" or "fail the job
with this message" from the class alone -- never by parsing a provider's text.
"""


class InvalidInputError(ValueError):
    """The request does not match the model's input schema -- caught before
    anything is sent, so nothing was paid for."""


class ProviderError(Exception):
    """A provider refused or failed a request. `retryable` says whether the
    same call may succeed later unchanged."""

    retryable = False


class ProviderAuthError(ProviderError):
    """The API key was rejected."""


class ProviderInputError(ProviderError):
    """The provider rejected the request itself (bad parameters, a file it
    will not take, no credits left)."""


class ProviderGenerationError(ProviderError):
    """The request was accepted, then the generation failed or was refused by
    moderation on the provider's side."""


class ProviderUnavailableError(ProviderError):
    """Network trouble, rate limiting or a 5xx that outlived the client's own
    retries."""

    retryable = True


class ProviderTimeoutError(ProviderError):
    """Still generating when the poll gave up; recovery finishes the job."""

    retryable = True
