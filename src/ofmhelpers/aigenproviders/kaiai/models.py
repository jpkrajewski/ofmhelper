"""kie.ai's wire format, as Pydantic models.

kie.ai answers HTTP 200 with its own `code` in the body; `KieEnvelope.raise_for_code`
turns that code into the shared error types, so nothing downstream reads it.
Unknown fields are ignored, so kie.ai adding one never breaks a parse.
"""

import json
from typing import NoReturn

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)

_OK = 200
_AUTH_CODES = frozenset({401, 403})
_NO_CREDITS = 402
# docs.kie.ai: "Rejected requests will not enter the queue" -- nothing was
# created, so the runner may send the same createTask again.
_RATE_LIMITED = 429
# How errors name the provider (its `Provider.label`).
_PROVIDER = "kie.ai"
# 455 is kie.ai's "under maintenance": retryable, but not resent on submit
# (a short backoff rarely outlasts a maintenance window).
_RETRYABLE_CODES = frozenset({408, 455, 500, 502, 503, 504})

# kie.ai's documented states: waiting -> queuing -> generating -> success/fail.
SUCCESS = "success"
FAIL = "fail"


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class KieEnvelope(_Lenient):
    code: int
    msg: str | None = None

    def raise_for_code(self) -> None:
        if self.code != _OK:
            raise_for(self.code, self.msg or "")


def raise_for(code: int, message: str, retry_after_s: float | None = None) -> NoReturn:
    """kie.ai's code (in the body, or the HTTP status) as the shared error.
    `retry_after_s`: an HTTP answer's Retry-After, for a 429."""
    text = f"{_PROVIDER}: {message or 'error'} (code {code})"
    if code in _AUTH_CODES:
        raise ProviderAuthError(_PROVIDER, text)
    if code == _NO_CREDITS:
        raise ProviderNoCreditsError(text)
    if code == _RATE_LIMITED:
        raise ProviderRateLimitError(text, retry_after_s)
    error: type[ProviderError] = ProviderInputError
    if code in _RETRYABLE_CODES or code >= 500:  # noqa: PLR2004
        error = ProviderUnavailableError
    raise error(text)


class KieTaskId(_Lenient):
    task_id: str = Field(alias="taskId")


class KieCreateResponse(KieEnvelope):
    data: KieTaskId | None = None


class KieRecord(_Lenient):
    task_id: str = Field(alias="taskId")
    state: str | None = None
    result_json: str | None = Field(default=None, alias="resultJson")
    fail_msg: str | None = Field(default=None, alias="failMsg")
    credits: float | None = Field(default=None, alias="creditsConsumed")
    cost_time_ms: int | None = Field(default=None, alias="costTime")

    @property
    def result_urls(self) -> list[str]:
        if not self.result_json:
            return []
        urls = json.loads(self.result_json).get("resultUrls") or []
        return [str(u) for u in urls]


class KieRecordResponse(KieEnvelope):
    data: KieRecord | None = None


class KieUploadData(_Lenient):
    download_url: str = Field(alias="downloadUrl")


class KieUploadResponse(_Lenient):
    success: bool = False
    code: int | None = None
    msg: str | None = None
    data: KieUploadData | None = None

    def url(self) -> str:
        if not self.success or self.data is None:
            raise_for(self.code or 401, self.msg or "upload rejected")
        return self.data.download_url
