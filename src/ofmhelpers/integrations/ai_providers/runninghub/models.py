"""RunningHub's wire format (the Standard Model API, `/openapi/v2/*`), as
Pydantic models, and its error codes as the shared error types.

RunningHub answers HTTP 200 with its own code in the body: `errorCode` on a
task, `code` on an upload. The code table is the docs' (runninghub.ai
runninghub-api-doc-en, "Error Codes" and the model-API table, 2026-10-31).
Unknown fields are ignored, so a field RunningHub adds never breaks a parse.
"""

from typing import NoReturn

from pydantic import Field

from ofmhelpers.integrations.ai_providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.ai_providers.http import Lenient

PROVIDER = "RunningHub"
# A wrong, unknown or missing key -- or one of a kind the API refuses: the
# model API takes Enterprise-Shared keys only (1014), a free account's key
# nothing (801).
_AUTH = frozenset({412, 435, 436, 801, 802, 806, 811, 1002, 1014, 1602})
_NO_CREDITS = frozenset({416, 812})
# Refused before a task was created (concurrency, queue full, rate): the
# runner may send the same submit again.
_RATE_LIMITED = frozenset({415, 421, 814, 1003, 1011, 1520})
_RETRYABLE = frozenset({500, 808, 1000, 1005, 1006, 1010, 1012, 1015, 1504, 1517, 1519})
# Content policy (1505: "Photorealistic real people are prohibited").
_REFUSED = frozenset({1501, 1505})
_HTTP_UNAUTHORIZED = 401
_HTTP_RATE_LIMITED = 429
# An HTTP status in the 5xx range; RunningHub's own codes (801, 1004 ...)
# are not HTTP statuses.
_HTTP_SERVER_ERRORS = range(500, 600)

PENDING = frozenset({"CREATE", "QUEUED", "RUNNING"})
SUCCESS = "SUCCESS"
FAILED = frozenset({"FAILED"})


def raise_for(code: int, message: str) -> NoReturn:
    """RunningHub's code (in the body, or an HTTP status) as the shared
    error."""
    text = f"{PROVIDER}: {message or 'error'} (code {code})"
    if code in _AUTH or code == _HTTP_UNAUTHORIZED:
        raise ProviderAuthError(PROVIDER, text)
    if code in _NO_CREDITS:
        raise ProviderNoCreditsError(text)
    if code in _RATE_LIMITED or code == _HTTP_RATE_LIMITED:
        raise ProviderRateLimitError(text)
    if code in _REFUSED:
        raise ProviderGenerationError(text)
    error: type[ProviderError] = ProviderInputError
    if code in _RETRYABLE or code in _HTTP_SERVER_ERRORS:
        error = ProviderUnavailableError
    raise error(text)


def is_no_credits(code: int) -> bool:
    return code in _NO_CREDITS


def as_code(value: str | int | None) -> int:
    """`errorCode` comes as a string ("806"), sometimes empty."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class RhResult(Lenient):
    url: str | None = None
    output_type: str | None = Field(default=None, alias="outputType")


class RhUsage(Lenient):
    """What the task cost, as RunningHub reports it (strings or numbers)."""

    consume_money: str | float | None = Field(default=None, alias="consumeMoney")
    consume_coins: str | float | None = Field(default=None, alias="consumeCoins")
    third_party_money: str | float | None = Field(
        default=None, alias="thirdPartyConsumeMoney"
    )
    cost_time_s: str | float | None = Field(default=None, alias="taskCostTime")


class RhTask(Lenient):
    """A submit's or a query's answer: the task, or an error code with no
    task id."""

    task_id: str = Field(default="", alias="taskId")
    status: str = ""
    error_code: str | int | None = Field(default=None, alias="errorCode")
    error_message: str | None = Field(default=None, alias="errorMessage")
    results: list[RhResult] | None = None
    usage: RhUsage | None = None

    @property
    def code(self) -> int:
        return as_code(self.error_code)

    def raise_for_error(self) -> None:
        """An answer with a code and no task status: the request was refused.
        Not "no task id": a query echoes the `taskId` it was asked about even
        then (806 for a wrong key, real API 2026-11-01). A FAILED task also
        carries a code; that is the task's state, read by `status`."""
        if not self.status and self.code:
            raise_for(self.code, self.error_message or "")

    @property
    def urls(self) -> list[str]:
        return [r.url for r in self.results or [] if r.url]

    @property
    def failure(self) -> str:
        reason = self.error_message or "generation failed"
        return f"{reason} (code {self.code})" if self.code else reason


class RhUploadData(Lenient):
    download_url: str | None = None
    filename: str | None = None


class RhUpload(Lenient):
    """`/openapi/v2/media/upload/binary`. The docs say `code` 200 on
    success, RunningHub's own SDK checks 0: success is the URL being
    there."""

    code: int | None = None
    msg: str | None = None
    message: str | None = None
    data: RhUploadData | None = None

    def url(self) -> str:
        if self.data is not None and self.data.download_url:
            return self.data.download_url
        text = self.msg or self.message or "upload refused"
        # A wrong key answers code 1, "ApiKey verification failed: ...".
        if "apikey" in text.lower().replace(" ", ""):
            raise ProviderAuthError(PROVIDER, f"{PROVIDER}: {text}")
        raise_for(self.code or 1, text)
