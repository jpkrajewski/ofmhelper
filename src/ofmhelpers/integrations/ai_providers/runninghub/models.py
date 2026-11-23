# Copyright (C) 2026 jpkrajewski

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
_AUTH = frozenset({412, 435, 436, 801, 802, 806, 811, 1002, 1014, 1602})
_NO_CREDITS = frozenset({416, 812})
_RATE_LIMITED = frozenset({415, 421, 814, 1003, 1011, 1520})
_RETRYABLE = frozenset({500, 808, 1000, 1005, 1006, 1010, 1012, 1015, 1504, 1517, 1519})
_REFUSED = frozenset({1501, 1505})
_HTTP_UNAUTHORIZED = 401
_HTTP_RATE_LIMITED = 429
_HTTP_SERVER_ERRORS = range(500, 600)

PENDING = frozenset({"CREATE", "QUEUED", "RUNNING"})
SUCCESS = "SUCCESS"
FAILED = frozenset({"FAILED"})


def raise_for(code: int, message: str) -> NoReturn:
    text = f"{PROVIDER}: {message or 'error'} (code {code})"
    if code in _AUTH or code == _HTTP_UNAUTHORIZED:
        raise ProviderAuthError(PROVIDER, text)
    if code in _NO_CREDITS:
        raise ProviderNoCreditsError(text, provider=PROVIDER)
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
    try:
        return int(value or 0)
    except TypeError, ValueError:  # silent: empty or not a number: no code
        return 0


class RhResult(Lenient):
    url: str | None = None
    output_type: str | None = Field(default=None, alias="outputType")


class RhUsage(Lenient):
    consume_money: str | float | None = Field(default=None, alias="consumeMoney")
    consume_coins: str | float | None = Field(default=None, alias="consumeCoins")
    third_party_money: str | float | None = Field(
        default=None,
        alias="thirdPartyConsumeMoney",
    )
    cost_time_s: str | float | None = Field(default=None, alias="taskCostTime")


class RhTask(Lenient):
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
    code: int | None = None
    msg: str | None = None
    message: str | None = None
    data: RhUploadData | None = None

    def url(self) -> str:
        if self.data is not None and self.data.download_url:
            return self.data.download_url
        text = self.msg or self.message or "upload refused"
        if "apikey" in text.lower().replace(" ", ""):
            raise ProviderAuthError(PROVIDER, f"{PROVIDER}: {text}")
        raise_for(self.code or 1, text)
