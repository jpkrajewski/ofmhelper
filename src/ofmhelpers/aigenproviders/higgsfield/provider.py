"""Higgsfield behind the shared `Provider` interface, on its official SDK
(`higgsfield-client`, docs: how-to/sdk).

Public SDK calls only: `SyncClient(api_key=...)` per account, `upload_file`,
`submit`, and a request controller's `status` / `get`. The SDK raises one
`HiggsfieldClientError` for every HTTP failure (the httpx error is its cause)
and lets httpx transport errors through; `_mapped_errors` turns both into the
shared error types.

The SDK retries 408/429/5xx itself, a submit included, and sends no
`Idempotency-Key` -- so a submit that reached Higgsfield and then failed can
in rare cases be charged twice. A job never resubmits once its request is
accepted (the runner resumes the remembered request id).
"""

import hashlib
import mimetypes
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import requests
from higgsfield_client import Completed, HiggsfieldClientError, SyncClient
from higgsfield_client.types_ import NSFW, Cancelled, Failed

from ofmhelpers.aigenproviders.base import Provider, TaskState, register_provider
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderInputError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.higgsfield.models import HiggsfieldRequest
from ofmhelpers.aigenproviders.spec import ModelSpec, Payload
from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

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
_AUTH_STATUSES = frozenset({401, 403})
_RETRYABLE_STATUSES = frozenset({408, 429})
_SERVER_ERROR = 500
_HTTP_ERROR = 400
_ENDED = (Completed, Failed, NSFW, Cancelled)


@contextmanager
def _mapped_errors(action: str) -> Iterator[None]:
    try:
        yield
    except HiggsfieldClientError as exc:
        cause = exc.__cause__
        code = (
            cause.response.status_code
            if isinstance(cause, httpx.HTTPStatusError)
            else 0
        )
        message = f"Higgsfield {action}: {exc} (HTTP {code or '?'})"
        error: type[ProviderError] = ProviderInputError
        if code in _AUTH_STATUSES:
            error = ProviderAuthError
        elif code in _RETRYABLE_STATUSES or code >= _SERVER_ERROR or not code:
            error = ProviderUnavailableError
        raise error(message) from exc
    except httpx.TransportError as exc:
        msg = f"Higgsfield {action}: unreachable ({exc.__class__.__name__})"
        raise ProviderUnavailableError(msg) from exc


@register_provider
class HiggsfieldProvider(Provider):
    name = "higgsfield"
    label = "Higgsfield"
    rank = 10

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        s = settings.higgsfield
        self.client = SyncClient(
            base_url=s.base_url, timeout=s.request_timeout_s, api_key=api_key
        )

    @classmethod
    def default_key(cls, admin: bool) -> str | None:
        s = settings.higgsfield
        return s.api_key_admin if admin else s.api_key_va

    def _upload_cache_key(self, path: Path) -> str:
        """Per account (uploads are), without the key itself in Redis."""
        account = hashlib.sha256(self.api_key.encode()).hexdigest()[:16]
        return f"higgsfield:upload:{account}:{path}"

    def upload(self, path: Path) -> str:
        """Memoized like kie.ai's: a reference reused across generations is
        uploaded once, and a cached URL is confirmed live before reuse."""
        content_type = mimetypes.guess_type(path.name)[0] or ""
        if content_type not in UPLOAD_TYPES:
            msg = (
                f"Higgsfield takes JPEG/PNG/WebP/GIF images, WAV audio and MP4 "
                f"video -- not {path.name}"
            )
            raise ProviderInputError(msg)
        cache_key = self._upload_cache_key(path)
        cached = get_text(cache_key)
        if cached is not None and _live(cached):
            return cached
        delete_text(cache_key)
        with _mapped_errors("upload"):
            url = self.client.upload_file(path)
        logger.info("higgsfield upload done: %s -> %s", path, url)
        set_text(cache_key, url, settings.higgsfield.upload_cache_ttl_s)
        return url

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        del idempotency_key  # the SDK's submit takes no headers; see module doc
        with _mapped_errors("submit"):
            return self.client.submit(spec.model_id, payload).request_id

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
        response = requests.head(
            url, timeout=settings.kieai.remote_check_timeout_s, allow_redirects=True
        )
    except requests.RequestException:
        return False
    return response.status_code < _HTTP_ERROR
