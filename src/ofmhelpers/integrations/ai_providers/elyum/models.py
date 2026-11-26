# Copyright (C) 2026 jpkrajewski

from typing import NoReturn

from pydantic import Field

from ofmhelpers.integrations.ai_providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.ai_providers.http import Lenient

PROVIDER = "Elyum"

_AUTH = frozenset({"auth", "scope"})
_NO_MONEY = frozenset(
    {"insufficient_balance", "key_cap", "member_budget", "client_budget"},
)
_TRY_AGAIN = frozenset(
    {
        "concurrency",
        "rate_limited",
        "model_unavailable",
        "option_unavailable",
        "maintenance",
    },
)
_HTTP_AUTH = frozenset({401, 403})
_HTTP_PAYMENT_REQUIRED = 402
_HTTP_TOO_MANY = 429
_HTTP_SERVER_ERRORS = range(500, 600)

PENDING = frozenset({"queued", "running"})
SUCCEEDED = "succeeded"
FAILED = frozenset({"failed", "canceled", "cancelled"})
_REFUSED = "content_refused"


class ElyumError(Lenient):
    code: str = ""
    message: str = ""
    param: str | None = None
    fix: str | None = None
    retry_after: float | None = None
    request_id: str | None = None


class ElyumErrorBody(Lenient):
    error: ElyumError | None = None


def raise_for(status: int, text: str) -> NoReturn:
    try:
        error = ElyumErrorBody.model_validate_json(text).error or ElyumError()
    except ValueError:  # silent: not JSON: the text is the message
        error = ElyumError(message=text[:200])
    detail = error.message or "error"
    if error.param:
        detail = f"{detail} [{error.param}]"
    message = f"{PROVIDER}: {detail} ({error.code or 'error'}, HTTP {status})"
    if error.code in _AUTH or status in _HTTP_AUTH:
        raise ProviderAuthError(PROVIDER, message)
    if error.code in _NO_MONEY or status == _HTTP_PAYMENT_REQUIRED:
        no_money = f"{PROVIDER}: insufficient balance. {message}"
        raise ProviderNoCreditsError(no_money, provider=PROVIDER)
    if error.code in _TRY_AGAIN or status == _HTTP_TOO_MANY:
        raise ProviderRateLimitError(message, retry_after_s=error.retry_after)
    failure: type[ProviderError] = ProviderInputError
    if status in _HTTP_SERVER_ERRORS:
        failure = ProviderUnavailableError
    raise failure(message)


class ElyumFile(Lenient):
    id: str
    type: str | None = None
    bytes: int | None = None


class ElyumOutput(Lenient):
    index: int = 0
    url: str | None = None
    type: str | None = None
    duration: float | None = None


class ElyumUsage(Lenient):
    usd: float | None = None
    billed: str | None = None


class ElyumGenError(Lenient):
    code: str = ""
    message: str = ""


class ElyumGeneration(Lenient):
    id: str
    status: str
    outputs: list[ElyumOutput] = Field(default_factory=list)
    usage: ElyumUsage | None = None
    error: ElyumGenError | None = None

    @property
    def urls(self) -> list[str]:
        return [o.url for o in sorted(self.outputs, key=lambda o: o.index) if o.url]

    @property
    def failure(self) -> str:
        if self.error is None:
            return f"{PROVIDER}: generation {self.status}"
        reason = self.error.message or self.error.code or self.status
        if self.error.code == _REFUSED:
            return f"{PROVIDER}: flagged by moderation: {reason} ({self.error.code})"
        return f"{PROVIDER}: {reason} ({self.error.code or self.status})"
