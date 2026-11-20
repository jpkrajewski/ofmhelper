import hashlib
import json
import math
import mimetypes
from contextlib import contextmanager
from functools import cached_property
lazy from collections.abc import Iterator
lazy from pathlib import Path

import httpx
import requests  # noqa: F401
from higgsfield_client import Completed, HiggsfieldClientError, SyncClient
from higgsfield_client.http.retry import ExponentialBackoffRetry
from higgsfield_client.http.transport import HttpTransport
from higgsfield_client.types_ import NSFW, Cancelled, Failed

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.base import (
    Provider,
    TaskState,
    register_provider,
)
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.ai_providers.higgsfield.models import (
    HiggsfieldRefusal,
    HiggsfieldRefusalDetail,
    HiggsfieldRequest,
    HiggsfieldSubmitted,
)
from ofmhelpers.integrations.ai_providers.uploads import (
    COPYABLE_SUFFIXES,
    memoized,
    reference_copy,
    remote_file_exists,
    upload_cache_key,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media.video_mp4 import CONVERTIBLE_SUFFIXES
lazy from ofmhelpers.integrations.ai_providers.higgsfield.fake import (
    install as install_fake,
)
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

logger = get_logger(__name__)

UPLOAD_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "audio/wav",
        "audio/x-wav",
        "video/mp4",
    }
)
mimetypes.add_type("image/webp", ".webp")
_KEY_REFUSED = 401
_KEY_DISABLED = 403
_NO_BALANCE = frozenset({402, 403})
_RATE_LIMITED = 429
_TIMED_OUT = 408
_SERVER_ERROR = 500
_SUBMIT_RESENT = frozenset({408, 500, 502, 503, 504})
_ENDED = (Completed, Failed, NSFW, Cancelled)


class _Client(SyncClient):
    @cached_property
    def _submit_transport(self) -> HttpTransport:
        retry = ExponentialBackoffRetry(retryable_status_codes=set(_SUBMIT_RESENT))
        return HttpTransport(self._client, retry)

    def submit_keyed(self, application: str, arguments: Payload, key: str) -> str:
        response = self._submit_transport.request(
            "POST",
            application,
            json=arguments,
            timeout=self.timeout,
            headers={"Idempotency-Key": key},
        )
        return HiggsfieldSubmitted.model_validate(response.json()).request_id


def _seconds(value: str) -> float | None:
    try:
        seconds = float(value)
    except ValueError:  # silent: not a number
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _retry_after(response: httpx.Response) -> float | None:
    header = _seconds(response.headers.get("Retry-After", ""))
    if header is not None:
        return header
    try:
        detail = HiggsfieldRefusal.model_validate(response.json()).detail
    except ValueError:  # silent: no detail given
        return None
    if isinstance(detail, HiggsfieldRefusalDetail):
        return detail.retry_after_seconds
    return None


def _from_api(response: httpx.Response) -> bool:
    return response.request.url.host == httpx.URL(settings.higgsfield.base_url).host


def _refused(action: str, exc: HiggsfieldClientError) -> ProviderError:
    cause = exc.__cause__
    response = cause.response if isinstance(cause, httpx.HTTPStatusError) else None
    code = response.status_code if response is not None else 0
    message = f"Higgsfield {action}: {exc} (HTTP {code or '?'})"
    api = response is not None and _from_api(response)
    disabled = response is not None and "no-store" in response.headers.get(
        "Cache-Control", ""
    )
    if api and (code == _KEY_REFUSED or (code == _KEY_DISABLED and disabled)):
        return ProviderAuthError(HiggsfieldProvider.label, message)
    if api and code in _NO_BALANCE:
        msg = f"Higgsfield {action}: insufficient balance -- {exc} (HTTP {code})"
        return ProviderNoCreditsError(msg, provider=HiggsfieldProvider.label)
    if response is not None and code == _RATE_LIMITED:
        return ProviderRateLimitError(message, retry_after_s=_retry_after(response))
    if code == _TIMED_OUT or code >= _SERVER_ERROR or not code:
        return ProviderUnavailableError(message)
    return ProviderInputError(message)


@contextmanager
def _mapped_errors(action: str) -> Iterator[None]:
    try:
        yield
    except HiggsfieldClientError as exc:
        raise _refused(action, exc) from exc
    except httpx.TransportError as exc:
        msg = f"Higgsfield {action}: unreachable ({exc.__class__.__name__})"
        never_sent = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
        raise ProviderUnavailableError(msg, maybe_made=not never_sent) from exc
    except (ValueError, KeyError, httpx.HTTPError) as exc:
        msg = f"Higgsfield {action}: unreadable answer ({exc.__class__.__name__})"
        raise ProviderUnavailableError(msg) from exc


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or ""


def _takes(path: Path) -> bool:
    return _content_type(path) in UPLOAD_TYPES


def _sendable(path: Path, max_bytes: int | None) -> Path:
    s = settings.higgsfield
    try:
        return reference_copy(
            path,
            "higgsfield",
            s.ref_image_limits.with_max_bytes(max_bytes),
            s.video_convert_timeout_s,
            _takes,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        if path.suffix.lower() not in CONVERTIBLE_SUFFIXES:
            raise
        logger.warning("higgsfield: %s not converted to MP4", path, exc_info=True)
        msg = f"Could not turn {path.name} into an MP4 video"
        raise ProviderInputError(msg) from exc


def _submit_key(job_key: str, model_id: str, payload: Payload) -> str:
    body = json.dumps(
        {"model": model_id, "input": payload}, sort_keys=True, separators=(",", ":")
    )
    return f"{job_key}-{hashlib.sha256(body.encode()).hexdigest()[:32]}"


@register_provider
class HiggsfieldProvider(Provider):
    name = "higgsfield"
    label = "Higgsfield"
    rank = 10
    idempotent_submit = True

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        s = settings.higgsfield
        self.client = _Client(
            base_url=s.base_url, timeout=s.request_timeout_s, api_key=api_key
        )
        if settings.infra.fake_externals:
            install_fake(self.client)

    @classmethod
    def default_key(cls) -> str | None:
        return settings.higgsfield.api_key_admin

    def _upload_cache_key(self, path: Path, max_bytes: int | None = None) -> str:
        return upload_cache_key("higgsfield", self.api_key, path, max_bytes)

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        if not _takes(path) and path.suffix.lower() not in COPYABLE_SUFFIXES:
            msg = (
                f"Higgsfield takes JPEG/PNG/WebP/GIF images, WAV audio and MP4 "
                f"video -- not {path.name}"
            )
            raise ProviderInputError(msg)
        return memoized(
            self._upload_cache_key(path, max_bytes),
            settings.higgsfield.upload_cache_ttl_s,
            lambda: self._upload(path, max_bytes),
            live=_live,
        )

    def _upload(self, path: Path, max_bytes: int | None) -> str:
        sent = _sendable(path, max_bytes)
        with _mapped_errors("upload"):
            url = self.client.upload_file(sent)
        logger.info("higgsfield upload done: %s -> %s", sent, url)
        return url

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        key = _submit_key(idempotency_key, spec.model_id, payload)
        try:
            with _mapped_errors("submit"):
                return self.client.submit_keyed(spec.model_id, payload, key)
        except ProviderUnavailableError as exc:
            if not isinstance(exc, ProviderRateLimitError):
                logger.warning(
                    "higgsfield submit for %s got no answer (%s): a request may "
                    "exist under Idempotency-Key %s",
                    idempotency_key,
                    exc,
                    key,
                    exc_info=True,
                )
            raise

    def status(self, task_id: str) -> TaskState:
        controller = self.client.get_request_controller(task_id)
        with _mapped_errors("status"):
            if not isinstance(controller.status(), _ENDED):
                return TaskState(state="pending")
            request = HiggsfieldRequest.model_validate(controller.get())
        if request.status == "completed":
            return TaskState(state="done", urls=request.urls)
        return TaskState(
            state="failed", error=request.failure, no_credits=request.out_of_credits
        )


def _live(url: str) -> bool:
    return remote_file_exists(url, settings.higgsfield.remote_check_timeout_s)
