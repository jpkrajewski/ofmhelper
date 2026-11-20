import json
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

_OK = 200
_AUTH_CODES = frozenset({401, 403})
_NO_CREDITS = 402
_RATE_LIMITED = 429
PROVIDER = "kie.ai"
_RETRYABLE_CODES = frozenset({408, 455, 500, 502, 503, 504})

SUCCESS = "success"
FAIL = "fail"


class KieEnvelope(Lenient):
    code: int
    msg: str | None = None

    def raise_for_code(self) -> None:
        if self.code != _OK:
            raise_for(self.code, self.msg or "")


def raise_for(code: int, message: str, retry_after_s: float | None = None) -> NoReturn:
    text = f"{PROVIDER}: {message or 'error'} (code {code})"
    if code in _AUTH_CODES:
        raise ProviderAuthError(PROVIDER, text)
    if code == _NO_CREDITS:
        raise ProviderNoCreditsError(text, provider=PROVIDER)
    if code == _RATE_LIMITED:
        raise ProviderRateLimitError(text, retry_after_s)
    error: type[ProviderError] = ProviderInputError
    if code in _RETRYABLE_CODES or code >= 500:  # noqa: PLR2004
        error = ProviderUnavailableError
    raise error(text)


class KieTaskId(Lenient):
    task_id: str = Field(alias="taskId")


class KieCreateResponse(KieEnvelope):
    data: KieTaskId | None = None


class KieRecord(Lenient):
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


class KieUploadData(Lenient):
    download_url: str = Field(alias="downloadUrl")


class KieUploadResponse(Lenient):
    success: bool = False
    code: int | None = None
    msg: str | None = None
    data: KieUploadData | None = None

    def url(self) -> str:
        if not self.success or self.data is None:
            raise_for(self.code or 401, self.msg or "upload rejected")
        return self.data.download_url
