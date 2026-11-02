"""Elyum's wire format (elyum.ai public API, `https://api.elyum.ai/v1`), as
Pydantic models, and its errors as the shared error types.

Elyum answers with real HTTP statuses and one error envelope
`{"error": {"code", "message", "param", "fix", "retryable", "retry_after",
"request_id"}}` (elyum.ai/open/docs#error-codes, 2026-11-02). Unknown
fields are ignored, so a field Elyum adds never breaks a parse.
"""

from typing import NoReturn

from pydantic import BaseModel, ConfigDict

from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

PROVIDER = "Elyum"

# 401 auth (missing/invalid key, a Studio MCP token), 403 scope (a key
# without generate + spend).
_AUTH = frozenset({"auth", "scope"})
# Nothing more can be spent on this key: no balance (402), the key's own
# 24 h cap, the member's or client's monthly budget (429, nothing held).
_NO_MONEY = frozenset(
    {"insufficient_balance", "key_cap", "member_budget", "client_budget"}
)
# Refused before anything was created or held: the same submit may go again
# (it carries an Idempotency-Key, so a duplicate is replayed, never paid).
_TRY_AGAIN = frozenset(
    {
        "concurrency",
        "rate_limited",
        "model_unavailable",
        "option_unavailable",
        "maintenance",
    }
)
_HTTP_AUTH = frozenset({401, 403})
_HTTP_PAYMENT_REQUIRED = 402
_HTTP_TOO_MANY = 429
_HTTP_SERVER_ERRORS = range(500, 600)

PENDING = frozenset({"queued", "running"})
SUCCEEDED = "succeeded"
# A finished generation's own error codes; `content_refused` = moderation.
_REFUSED = "content_refused"


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ElyumError(_Lenient):
    code: str = ""
    message: str = ""
    param: str | None = None
    fix: str | None = None
    retry_after: float | None = None
    request_id: str | None = None


class ElyumErrorBody(_Lenient):
    error: ElyumError | None = None


def raise_for(status: int, text: str) -> NoReturn:
    """An HTTP error answer (`text`: its body) as the shared error."""
    try:
        error = ElyumErrorBody.model_validate_json(text).error or ElyumError()
    except ValueError:
        error = ElyumError(message=text[:200])
    detail = error.message or "error"
    if error.param:
        detail = f"{detail} [{error.param}]"
    message = f"{PROVIDER}: {detail} ({error.code or 'error'}, HTTP {status})"
    if error.code in _AUTH or status in _HTTP_AUTH:
        raise ProviderAuthError(PROVIDER, message)
    if error.code in _NO_MONEY or status == _HTTP_PAYMENT_REQUIRED:
        # Worded so the card reads "No credits" for a cap or budget too.
        no_money = f"{PROVIDER}: insufficient balance. {message}"
        raise ProviderNoCreditsError(no_money)
    if error.code in _TRY_AGAIN or status == _HTTP_TOO_MANY:
        raise ProviderRateLimitError(message, retry_after_s=error.retry_after)
    failure: type[ProviderError] = ProviderInputError
    if status in _HTTP_SERVER_ERRORS:
        failure = ProviderUnavailableError
    raise failure(message)


class ElyumFile(_Lenient):
    """`POST /v1/files` (201): the id a generation's input names."""

    id: str
    type: str | None = None
    bytes: int | None = None


class ElyumOutput(_Lenient):
    index: int = 0
    url: str | None = None
    type: str | None = None
    duration: float | None = None


class ElyumUsage(_Lenient):
    usd: float | None = None
    billed: str | None = None


class ElyumGenError(_Lenient):
    code: str = ""
    message: str = ""


class ElyumGeneration(_Lenient):
    """`POST /v1/generations` and `GET /v1/generations/{id}`."""

    id: str
    status: str
    outputs: list[ElyumOutput] = []
    usage: ElyumUsage | None = None
    error: ElyumGenError | None = None

    @property
    def urls(self) -> list[str]:
        return [o.url for o in sorted(self.outputs, key=lambda o: o.index) if o.url]

    @property
    def failure(self) -> str:
        """Why it ended without a result, worded so the card's reason reads
        it ("content_refused" -> Flagged content)."""
        if self.error is None:
            return f"{PROVIDER}: generation {self.status}"
        reason = self.error.message or self.error.code or self.status
        if self.error.code == _REFUSED:
            return f"{PROVIDER}: flagged by moderation: {reason} ({self.error.code})"
        return f"{PROVIDER}: {reason} ({self.error.code or self.status})"
