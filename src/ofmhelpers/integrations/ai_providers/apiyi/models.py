"""APIYI's wire format (api.apiyi.com, docs.apiyi.com wan3 pages, 2026-11-06)
as Pydantic models, and its errors as the shared error types.

A submit is DashScope's own (`output.task_id`); a task read is APIYI's
(`GET /v1/tasks/{id}`: `status` submitted/in_progress/completed/failed,
`result_url`, `error.message` or `fail_reason`), not DashScope's
`SUCCEEDED`. Errors come either OpenAI-style from APIYI's gateway
(`{"error": {"message", "type", "code"}}`) or DashScope-style from upstream
(`{"code", "message", "request_id"}`); both are read. Unknown fields are
ignored, a body wrapped in `data` is unwrapped, and a text field answered
`null` or as a number is read as text, so a shape APIYI changes never makes
a paid task unreadable.
"""

import re
from typing import Annotated, Any, NoReturn

from pydantic import AliasChoices, BeforeValidator, Field, model_validator

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

PROVIDER = "APIYI"

# Lower-cased. APIYI's own statuses first; DashScope's native ones too, in
# case a task is answered unconverted (UNKNOWN: queried after 24 h, gone).
PENDING = frozenset(
    {"submitted", "in_progress", "queued", "pending", "running", "processing"}
)
COMPLETED = frozenset({"completed", "succeeded", "success"})
FAILED = frozenset({"failed", "canceled", "cancelled", "unknown"})

_HTTP_AUTH = 401
_HTTP_FORBIDDEN = 403
_HTTP_PAYMENT_REQUIRED = 402
_HTTP_TOO_MANY = 429
_HTTP_SERVER_ERRORS = range(500, 600)
# The gateway's words for an empty balance (one-api/new-api: "insufficient
# user quota", "用户额度不足"), in the account's own language too (dev
# 2026-11-06, an empty account: 403 "Niewystarczające saldo").
_NO_MONEY = re.compile(r"quota|额度|余额|balance|saldo|guthaben|solde", re.IGNORECASE)
# "Current group <g> has no available channels for model <m> under billing
# mode [...]" (dev 2026-11-06, a `default` group key asking for Wan 3.0).
_NO_CHANNEL = re.compile(r"no available channel|无可用渠道", re.IGNORECASE)
_GROUP = re.compile(r"group\s+(\S+)", re.IGNORECASE)
_MODEL = re.compile(r"for model\s+(\S+)", re.IGNORECASE)
_BILLING_MODE = re.compile(r"billing mode\s*\[([^\]]*)\]", re.IGNORECASE)
# Each key group's name starts so (Wan&HappyHorse, SeeDance2, default).
_GROUP_NAMES = {"wan": "wan", "seedance": "seedance", "default": "default"}
_PAY_AS_YOU_GO = "pay-as-you-go"
# DashScope's moderation codes (DataInspectionFailed, IPInfringementSuspect):
# matched on the code only -- a reason echoing a URL ("green_dress.jpg")
# must not read as moderation.
_REFUSED_CODE = re.compile(r"inspection|infringement", re.IGNORECASE)
# A failed task's code as APIYI really sends it: only inside the message,
# "[DataInspectionFailed] Green net check rejected image (input)", `error`
# {message, type: task_failed} with no `code` (prod 2026-11-07, job 1881dd9b:
# the card read "Failed" instead of "Flagged content"). `\w+` on purpose: a
# dotted code ("[InvalidParameter.DataInspection]", Alibaba's media
# download/format failure) stays in the text, since `_REFUSED_CODE` would
# read "inspection" in it as moderation -- narrow that before widening this.
_CODE_IN_MESSAGE = re.compile(r"^\[(\w+)\]\s*(.*)$", re.DOTALL)
# Alibaba could not fetch a reference from our link inside its own timeout:
# it answers "Failed to download <our link> (InvalidParameter)"
# (prod 2026-11-10 and 2026-11-12). The task is refunded; the link (its
# token) never goes on a card. Only our own hosted links (`hosting.link`:
# /files/ai/): any other failed download is not ours to send again.
_UNFETCHED = re.compile(r"failed to download\s+\S*/files/ai/", re.IGNORECASE)


def _text(value: Any) -> Any:
    """`null` as "", a number as its digits: a field typed text is never what
    makes an answer unreadable."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return value


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


def _read_error(status: int, text: str) -> tuple[str, str]:
    """(message for us, the provider's words) of an error answer."""
    try:
        error = ApiyiErrorBody.model_validate_json(text).as_error()
    except ValueError:
        error = ApiyiError(message=text[:200])
    detail = error.message or "error"
    code = error.code or error.type or "error"
    return f"{PROVIDER}: {detail} ({code}, HTTP {status})", f"{detail} {code}"


def _no_channel(message: str, said: str) -> NoReturn:
    """No channel for the model: this key's group or billing mode cannot reach
    it (a key that will never work: auth, a /clone-links batch stops), or
    the Wan group's channels are all down for now (nothing created: sent
    again after a wait)."""
    from ofmhelpers.integrations.ai_providers.apiyi.keys import (
        group_of,
    )

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
    status: int, text: str, *, forbidden_is_refusal: bool = False
) -> NoReturn:
    """A submit's HTTP error answer (`text`: its body) as the shared error.
    Only 401/403 are about the key or the money; `forbidden_is_refusal`: a
    403 that is not about money is moderation (Seedance: a realistic face)."""
    message, said = _read_error(status, text)
    if status == _HTTP_AUTH:
        raise ProviderAuthError(PROVIDER, message)
    if status == _HTTP_PAYMENT_REQUIRED or (
        status == _HTTP_FORBIDDEN and _NO_MONEY.search(said)
    ):
        no_money = f"{PROVIDER}: insufficient balance. {message}"
        raise ProviderNoCreditsError(no_money)
    if _NO_CHANNEL.search(said):
        _no_channel(message, said)
    if status == _HTTP_FORBIDDEN and forbidden_is_refusal:
        flagged = f"{PROVIDER}: flagged by moderation: {message}"
        raise ProviderGenerationError(flagged)
    if status == _HTTP_FORBIDDEN:
        # Docs: 403 = the key's group lacks Wan&HappyHorse, or it bills per
        # call; either way this key cannot run Wan 3.0.
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
    """A task read's HTTP error answer: only a key APIYI no longer knows (401)
    ends the wait; anything else is asked again until the deadline, then
    recovery -- a paid task is never given up on a read's error."""
    message, _said = _read_error(status, text)
    if status == _HTTP_AUTH:
        raise ProviderAuthError(PROVIDER, message)
    raise ProviderUnavailableError(message)


def _unwrapped(data: Any) -> Any:
    """A body APIYI wraps as `{"data": {...}}` read as the inner object."""
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


class ApiyiSubmit(Lenient):
    """`POST .../video-synthesis` (async): DashScope's `output.task_id`.
    A 200 says only that the task is queued: bad parameters or media fail
    it later, read on the task."""

    output: ApiyiSubmitOutput | None = None
    request_id: Text = ""
    # In case the gateway answers it at the top level.
    top_task_id: Text = Field(
        default="", validation_alias=AliasChoices("task_id", "id")
    )

    @model_validator(mode="before")
    @classmethod
    def _inner(cls, data: Any) -> Any:
        return _unwrapped(data)

    @property
    def task_id(self) -> str:
        return (self.output.task_id if self.output else "") or self.top_task_id


class ApiyiNativeOutput(Lenient):
    """DashScope's own task shape, in case APIYI hands it on unconverted."""

    task_status: Text = ""
    video_url: str | None = None
    code: Text = ""
    message: Text = ""


class ApiyiTask(Lenient):
    """`GET /v1/tasks/{id}`: success is `completed`, then `result_url` (an
    OSS link signed for 24 h, fetched without our key)."""

    task_id: Text = Field(default="", validation_alias=AliasChoices("task_id", "id"))
    status: Text = ""
    progress: str | int | float | None = None
    result_url: str | None = None
    error: ApiyiError | str | None = None
    fail_reason: Text = ""
    output: ApiyiNativeOutput | None = None

    @model_validator(mode="before")
    @classmethod
    def _inner(cls, data: Any) -> Any:
        return _unwrapped(data)

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
        """It failed because the upstream could not fetch one of our
        reference links: nothing made, refunded, worth sending again."""
        return _UNFETCHED.search(self._said) is not None

    @property
    def failure(self) -> str:
        """Why it ended without a result, worded so the card's reason reads
        it (a moderation code -> Flagged content)."""
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
        # A code with no words (DashScope's own `output.code` alone) still
        # says what it was.
        reason = reason or f"task {self.state or 'failed'}"
        if _REFUSED_CODE.search(code):
            return f"{PROVIDER}: flagged by moderation: {reason} ({code})"
        return f"{PROVIDER}: {reason}" + (f" ({code})" if code else "")
