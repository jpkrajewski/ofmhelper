# Copyright (C) 2026 jpkrajewski

import re
from typing import Annotated, Any, NoReturn

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field, model_validator

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
lazy from ofmhelpers.integrations.ai_providers.apiyi.keys import group_of

PROVIDER = "APIYI"

PENDING = frozenset(
    {"submitted", "in_progress", "queued", "pending", "running", "processing"},
)
COMPLETED = frozenset({"completed", "succeeded", "success"})
FAILED = frozenset({"failed", "canceled", "cancelled", "unknown"})

_HTTP_AUTH = 401
_HTTP_FORBIDDEN = 403
_HTTP_PAYMENT_REQUIRED = 402
_HTTP_TOO_MANY = 429
_HTTP_SERVER_ERRORS = range(500, 600)
_NO_MONEY = re.compile(r"quota|额度|余额|balance|saldo|guthaben|solde", re.IGNORECASE)
_NO_CHANNEL = re.compile(r"no available channel|无可用渠道", re.IGNORECASE)
_GROUP = re.compile(r"group\s+(\S+)", re.IGNORECASE)
_MODEL = re.compile(r"for model\s+(\S+)", re.IGNORECASE)
_BILLING_MODE = re.compile(r"billing mode\s*\[([^\]]*)\]", re.IGNORECASE)
_GROUP_NAMES = {"wan": "wan", "seedance": "seedance", "default": "default"}
_PAY_AS_YOU_GO = "pay-as-you-go"
_REFUSED_CODE = re.compile(r"inspection|infringement", re.IGNORECASE)
_CODE_IN_MESSAGE = re.compile(r"^\[(\w+)\]\s*(.*)$", re.DOTALL)
_UNFETCHED = re.compile(r"failed to download\s+\S*/files/ai/", re.IGNORECASE)


def _text(value: str | float | None) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return value
    msg = "expected text"
    raise ValueError(msg)


Text = Annotated[str, BeforeValidator(_text)]


class ApiyiError(Lenient):
    message: Text = ""
    type: Text = ""
    code: Text = ""


class ApiyiErrorBody(Lenient):
    error: ApiyiError | str | None = None
    code: Text = ""
    message: Text = ""

    def as_error(self) -> ApiyiError:
        if isinstance(self.error, ApiyiError):
            return self.error
        return ApiyiError(message=self.error or self.message, code=self.code)


class ReadError(BaseModel, frozen=True):
    message: str
    said: str


def _read_error(status: int, text: str) -> ReadError:
    try:
        error = ApiyiErrorBody.model_validate_json(text).as_error()
    except ValueError:  # silent: not JSON: the text is the message
        error = ApiyiError(message=text[:200])
    detail = error.message or "error"
    code = error.code or error.type or "error"
    return ReadError(
        message=f"{PROVIDER}: {detail} ({code}, HTTP {status})",
        said=f"{detail} {code}",
    )


def _no_channel(message: str, said: str) -> NoReturn:
    group = _GROUP.search(said)
    model = _MODEL.search(said)
    expected = _GROUP_NAMES[group_of(model.group(1) if model else "wan")]
    modes = _BILLING_MODE.search(said)
    wrong_group = group is not None and not group.group(1).lower().startswith(expected)
    per_call = modes is not None and _PAY_AS_YOU_GO not in modes.group(1).lower()
    if wrong_group or per_call:
        raise ProviderAuthError(
            PROVIDER,
            f"{message}: needs a pay-as-you-go key of the Wan&HappyHorse group",
        )
    raise ProviderRateLimitError(message)


def raise_for(
    status: int,
    text: str,
    *,
    forbidden_is_refusal: bool = False,
) -> NoReturn:
    read = _read_error(status, text)
    message, said = read.message, read.said
    if status == _HTTP_AUTH:
        raise ProviderAuthError(PROVIDER, message)
    if status == _HTTP_PAYMENT_REQUIRED or (
        status == _HTTP_FORBIDDEN and _NO_MONEY.search(said)
    ):
        no_money = f"{PROVIDER}: insufficient balance. {message}"
        raise ProviderNoCreditsError(no_money, provider=PROVIDER)
    if _NO_CHANNEL.search(said):
        _no_channel(message, said)
    if status == _HTTP_FORBIDDEN and forbidden_is_refusal:
        flagged = f"{PROVIDER}: flagged by moderation: {message}"
        raise ProviderGenerationError(flagged)
    if status == _HTTP_FORBIDDEN:
        raise ProviderAuthError(
            PROVIDER,
            f"{message}: needs a pay-as-you-go key of the Wan&HappyHorse group",
        )
    if status == _HTTP_TOO_MANY:
        raise ProviderRateLimitError(message)
    failure: type[ProviderError] = ProviderInputError
    if status in _HTTP_SERVER_ERRORS:
        failure = ProviderUnavailableError
    raise failure(message)


def raise_for_read(status: int, text: str) -> NoReturn:
    message = _read_error(status, text).message
    if status == _HTTP_AUTH:
        raise ProviderAuthError(PROVIDER, message)
    raise ProviderUnavailableError(message)


class Unwrapped(Lenient):
    @model_validator(mode="before")
    @classmethod
    def _inner(cls, data: Any) -> Any:
        if (
            isinstance(data, dict)
            and "status" not in data
            and isinstance(data.get("data"), dict)
        ):
            return data["data"]
        return data


class ApiyiSubmitOutput(Lenient):
    task_id: Text = ""
    task_status: Text = ""


class ApiyiSubmit(Unwrapped):
    output: ApiyiSubmitOutput | None = None
    request_id: Text = ""
    top_task_id: Text = Field(
        default="",
        validation_alias=AliasChoices("task_id", "id"),
    )

    @property
    def task_id(self) -> str:
        return (self.output.task_id if self.output else "") or self.top_task_id


class ApiyiNativeOutput(Lenient):
    task_status: Text = ""
    video_url: str | None = None
    code: Text = ""
    message: Text = ""


class ApiyiTask(Unwrapped):
    task_id: Text = Field(default="", validation_alias=AliasChoices("task_id", "id"))
    status: Text = ""
    progress: str | int | float | None = None
    result_url: str | None = None
    error: ApiyiError | str | None = None
    fail_reason: Text = ""
    output: ApiyiNativeOutput | None = None

    @property
    def state(self) -> str:
        native = self.output.task_status if self.output else ""
        return (self.status or native).strip().lower()

    @property
    def urls(self) -> list[str]:
        url = self.result_url or (self.output.video_url if self.output else None)
        return [url] if url else []

    @property
    def _said(self) -> str:
        message = self.error.message if isinstance(self.error, ApiyiError) else ""
        plain = self.error if isinstance(self.error, str) else ""
        native = self.output.message if self.output else ""
        return f"{message} {plain} {self.fail_reason} {native}"

    @property
    def unfetched(self) -> bool:
        return _UNFETCHED.search(self._said) is not None

    @property
    def failure(self) -> str:
        if self.unfetched:
            return f"{PROVIDER}: Chinese server is slow, try again later"
        if isinstance(self.error, ApiyiError):
            reason, code = self.error.message, self.error.code
        else:
            reason, code = self.error or "", ""
        native = self.output or ApiyiNativeOutput()
        reason = reason or self.fail_reason or native.message
        code = code or native.code
        cited = _CODE_IN_MESSAGE.match(reason.strip())
        if cited and not code:
            code, reason = cited.group(1), cited.group(2) or cited.group(1)
        if not reason and not code:
            return f"{PROVIDER}: task {self.state or 'failed'}"
        reason = reason or f"task {self.state or 'failed'}"
        if _REFUSED_CODE.search(code):
            return f"{PROVIDER}: flagged by moderation: {reason} ({code})"
        return f"{PROVIDER}: {reason}" + (f" ({code})" if code else "")
