"""Higgsfield behind the shared `Provider` interface, on its official SDK
(`higgsfield-client`, docs: how-to/sdk).

Public SDK calls: `SyncClient(api_key=...)` per account, `upload_file` and a
request controller's `status` / `get`. The submit is `_Client.submit_keyed`
(the SDK's own transport class): it sends an `Idempotency-Key`
(docs: concepts/idempotency), so a resend after a 408/5xx -- the transport
resends those at once -- returns the request a lost answer already made
instead of paying for another. A 429 is not resent here: Higgsfield made
nothing, and the runner waits its Retry-After. The SDK raises one
`HiggsfieldClientError` for every HTTP failure (the httpx error is its cause)
and lets httpx transport errors through; `_mapped_errors` turns both, and
answers nothing here can read, into the shared error types.
"""

import hashlib
import json
import math
import mimetypes
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path

import httpx
import requests
from higgsfield_client import Completed, HiggsfieldClientError, SyncClient
from higgsfield_client.http.retry import ExponentialBackoffRetry
from higgsfield_client.http.transport import HttpTransport
from higgsfield_client.types_ import NSFW, Cancelled, Failed

from ofmhelpers.aigenproviders.base import (
    Provider,
    TaskState,
    register_provider,
    upload_lock,
)
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.higgsfield.models import (
    HiggsfieldRefusal,
    HiggsfieldRefusalDetail,
    HiggsfieldRequest,
    HiggsfieldSubmitted,
)
from ofmhelpers.aigenproviders.spec import ModelSpec, Payload
from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound
from ofmhelpers.utils.image_fit import FITTABLE_SUFFIXES, fitted

logger = get_logger(__name__)

# The upload endpoint's accepted types (docs: concepts/file-uploads).
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
# The image's Python (3.12, no /etc/mime.types) does not know .webp: a WebP
# subject was refused as "not WebP" (dev 2026-10-25).
mimetypes.add_type("image/webp", ".webp")
# docs: concepts/errors. 403 is "Insufficient balance", unless it carries
# `Cache-Control: no-store` -- a disabled or expired key (api-reference/headers).
_KEY_REFUSED = 401
_KEY_DISABLED = 403
_NO_BALANCE = frozenset({402, 403})
_RATE_LIMITED = 429
_TIMED_OUT = 408
_SERVER_ERROR = 500
_HTTP_ERROR = 400
# What a keyed submit is resent on at once: an answer that may have been lost
# after the request was made (the key returns that request).
_SUBMIT_RESENT = frozenset({408, 500, 502, 503, 504})
_ENDED = (Completed, Failed, NSFW, Cancelled)


class _Client(SyncClient):
    """The SDK client plus a submit that carries an Idempotency-Key."""

    @cached_property
    def _submit_transport(self) -> HttpTransport:
        # Built at first use, so it wraps whatever `_client` holds by then:
        # the fake's (`install`) or a test's.
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
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _retry_after(response: httpx.Response) -> float | None:
    """A 429's wait: the Retry-After header (seconds, docs: api-reference/
    headers), else the body's `detail.retry_after_seconds` (rate-limits)."""
    header = _seconds(response.headers.get("Retry-After", ""))
    if header is not None:
        return header
    try:
        detail = HiggsfieldRefusal.model_validate(response.json()).detail
    except ValueError:
        return None
    if isinstance(detail, HiggsfieldRefusalDetail):
        return detail.retry_after_seconds
    return None


def _from_api(response: httpx.Response) -> bool:
    """Higgsfield's API answered, not the storage behind a presigned PUT
    (whose 403 is a stale signature, never a key or the balance)."""
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
        return ProviderNoCreditsError(msg)
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
        raise ProviderUnavailableError(msg) from exc
    except (ValueError, KeyError, httpx.HTTPError) as exc:
        # After TransportError (an HTTPError): the SDK's "Unknown status", a
        # 200 that is not JSON, a shape the models refuse. The request may
        # exist, so it is retryable -- the job stays remembered, never failed
        # and forgotten after it was paid.
        msg = f"Higgsfield {action}: unreadable answer ({exc.__class__.__name__})"
        raise ProviderUnavailableError(msg) from exc


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or ""


def _needs_jpeg(path: Path) -> bool:
    """An image Higgsfield does not take that a JPEG copy stands in for
    (BMP, TIFF: the asset store accepts them)."""
    return (
        _content_type(path) not in UPLOAD_TYPES
        and path.suffix.lower() in FITTABLE_SUFFIXES
    )


def _submit_key(job_key: str, model_id: str, payload: Payload) -> str:
    """One generation intent (docs: concepts/idempotency): the job's key plus
    a digest of exactly what is sent. A resend of the same request gets the
    request it made; changed inputs (a reference uploaded again, a job id a
    later job reuses) are a new intent instead of Higgsfield's 422."""
    body = json.dumps(
        {"model": model_id, "input": payload}, sort_keys=True, separators=(",", ":")
    )
    return f"{job_key}-{hashlib.sha256(body.encode()).hexdigest()[:32]}"


@register_provider
class HiggsfieldProvider(Provider):
    name = "higgsfield"
    label = "Higgsfield"
    rank = 10

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        s = settings.higgsfield
        self.client = _Client(
            base_url=s.base_url, timeout=s.request_timeout_s, api_key=api_key
        )
        if settings.infra.fake_externals:
            from ofmhelpers.aigenproviders.higgsfield.fake import install

            install(self.client)

    @classmethod
    def default_key(cls) -> str | None:
        return settings.higgsfield.api_key_admin

    def _upload_cache_key(self, path: Path) -> str:
        """Per account (uploads are), without the key itself in Redis."""
        account = hashlib.sha256(self.api_key.encode()).hexdigest()[:16]
        return f"higgsfield:upload:{account}:{path}"

    def upload(self, path: Path) -> str:
        """Memoized like kie.ai's: a reference reused across generations is
        uploaded once, and a cached URL is confirmed live before reuse. The
        cache and its lock are keyed by `path` itself and looked up first: a
        batch makes one shrunk copy and one upload, and a resumed run whose
        subject is gone still gets its URL. An image over `ref_image_limits`
        or in a format Higgsfield does not take goes up as a JPEG copy."""
        as_jpeg = _needs_jpeg(path)
        if not as_jpeg and _content_type(path) not in UPLOAD_TYPES:
            msg = (
                f"Higgsfield takes JPEG/PNG/WebP/GIF images, WAV audio and MP4 "
                f"video -- not {path.name}"
            )
            raise ProviderInputError(msg)
        cache_key = self._upload_cache_key(path)
        with upload_lock(cache_key):
            cached = get_text(cache_key)
            if cached is not None and _live(cached):
                return cached
            delete_text(cache_key)
            sent = fitted(
                path,
                Path(settings.aigen.out_dir) / "higgsfield" / "fitted",
                settings.higgsfield.ref_image_limits,
                jpeg=as_jpeg,
            )
            with _mapped_errors("upload"):
                url = self.client.upload_file(sent)
            logger.info("higgsfield upload done: %s -> %s", sent, url)
            set_text(cache_key, url, settings.higgsfield.upload_cache_ttl_s)
            return url

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        key = _submit_key(idempotency_key, spec.model_id, payload)
        try:
            with _mapped_errors("submit"):
                return self.client.submit_keyed(spec.model_id, payload, key)
        except ProviderUnavailableError as exc:
            if not isinstance(exc, ProviderRateLimitError):  # that one made none
                logger.warning(
                    "higgsfield submit for %s got no answer (%s): a request may "
                    "exist under Idempotency-Key %s",
                    idempotency_key,
                    exc,
                    key,
                )
            raise

    def status(self, task_id: str) -> TaskState:
        controller = self.client.get_request_controller(task_id)
        with _mapped_errors("status"):
            if not isinstance(controller.status(), _ENDED):
                return TaskState(state="pending")
            # Ended: `get` returns at once with the full body (outputs/error).
            request = HiggsfieldRequest.model_validate(controller.get())
        if request.status == "completed":
            return TaskState(state="done", urls=request.urls)
        return TaskState(state="failed", error=request.failure)


def _live(url: str) -> bool:
    """Fails closed: any doubt means upload again."""
    try:
        response = outbound.head(
            url, timeout=settings.kieai.remote_check_timeout_s, allow_redirects=True
        )
    except requests.RequestException:
        return False
    return response.status_code < _HTTP_ERROR
