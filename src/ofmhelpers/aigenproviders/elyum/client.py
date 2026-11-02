"""Elyum's public API (elyum.ai/open/docs): upload a reference file, start a
generation, read it back. Single calls only; waiting, resuming and
downloading are `runner.py`'s.

Every submit carries our idempotency key as `Idempotency-Key`: a retry of
the same body returns the generation already made (`Idempotent-Replayed`),
never a second paid one. Through `utils.outbound`, so OFM_FAKE_EXTERNALS
answers it from `fake.py`.
"""

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any

import requests

from ofmhelpers.aigenproviders.base import upload_lock
from ofmhelpers.aigenproviders.elyum.models import (
    PENDING,
    PROVIDER,
    ElyumFile,
    ElyumGeneration,
    raise_for,
)
from ofmhelpers.aigenproviders.errors import (
    ProviderInputError,
    ProviderUnavailableError,
)
from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound
from ofmhelpers.utils.image_fit import FITTABLE_SUFFIXES, fitted
from ofmhelpers.utils.video_mp4 import CONVERTIBLE_SUFFIXES, as_mp4

logger = get_logger(__name__)

API = "/v1"
_HTTP_ERROR = 400
# What POST /v1/files takes (PNG, JPG, WebP, MP4, MOV, WebM, MP3, WAV).
ACCEPTED_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".mp3", ".wav"}
)
# Idempotency-Key: up to 255 printable characters, no spaces.
_KEY_UNSAFE = re.compile(r"[^\x21-\x7e]")
_KEY_MAX = 255


def idempotency_header(key: str) -> str:
    return _KEY_UNSAFE.sub("_", key)[:_KEY_MAX]


class ElyumClient:
    """One Elyum account (its API key)."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._base = settings.elyum.base_url.rstrip("/") + API
        self._http = outbound.mount(requests.Session())
        self._http.headers["Authorization"] = f"Bearer {api_key}"

    def _call(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """One request; a transport failure is `ProviderUnavailableError`, an
        HTTP error the error of its code."""
        try:
            response = self._http.request(
                method,
                f"{self._base}/{path}",
                timeout=settings.elyum.request_timeout_s,
                **kwargs,
            )
        except requests.RequestException as exc:
            msg = f"{PROVIDER} unreachable: {exc.__class__.__name__}"
            raise ProviderUnavailableError(msg) from exc
        if response.status_code >= _HTTP_ERROR:
            raise_for(response.status_code, response.text[:2000])
        return response

    @staticmethod
    def _generation(response: requests.Response) -> ElyumGeneration:
        """A 2xx that is not a generation is Elyum misbehaving: unavailable,
        so a poll keeps trying and a paid request stays remembered."""
        try:
            return ElyumGeneration.model_validate_json(response.content)
        except ValueError as exc:  # JSON and validation errors alike
            msg = f"{PROVIDER}: unreadable answer ({exc.__class__.__name__})"
            raise ProviderUnavailableError(msg) from exc

    # --- upload -----------------------------------------------------------

    def _cache_key(self, path: Path, max_bytes: int | None) -> str:
        account = hashlib.sha256(self.api_key.encode()).hexdigest()[:16]
        key = f"elyum:upload:{account}:{path}"
        return f"{key}:{max_bytes}" if max_bytes else key

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        """The `file_...` id of `path` on this account; one upload per file
        across workers, reused while cached."""
        cache_key = self._cache_key(path, max_bytes)
        with upload_lock(cache_key):
            cached = get_text(cache_key)
            if cached is not None:
                return cached
            file_id = self._upload(self._sendable(path, max_bytes))
            set_text(cache_key, file_id, settings.elyum.upload_cache_ttl_s)
            logger.info("elyum upload %s -> %s", path.name, file_id)
            return file_id

    @staticmethod
    def _sendable(path: Path, max_bytes: int | None) -> Path:
        """`path`, or the copy of it Elyum takes: an .mkv/.m4v as MP4, a
        BMP/TIFF or an image over the limits as JPEG. Refused here, before
        any call, when no copy fits."""
        suffix = path.suffix.lower()
        out = Path(settings.aigen.out_dir) / "elyum"
        try:
            if suffix in CONVERTIBLE_SUFFIXES and suffix not in ACCEPTED_SUFFIXES:
                sent = as_mp4(path, out / "mp4", settings.elyum.video_convert_timeout_s)
            else:
                sent = fitted(
                    path,
                    out / "fitted",
                    settings.elyum.ref_image_limits.with_max_bytes(max_bytes),
                    jpeg=suffix in FITTABLE_SUFFIXES
                    and suffix not in ACCEPTED_SUFFIXES,
                )
        except (RuntimeError, OSError, ValueError) as exc:
            logger.warning("elyum: %s could not be prepared", path, exc_info=True)
            msg = f"Could not prepare {path.name} for {PROVIDER}"
            raise ProviderInputError(msg) from exc
        if sent.suffix.lower() not in ACCEPTED_SUFFIXES:
            msg = (
                f"{PROVIDER} takes PNG/JPG/WebP images, MP4/MOV/WebM video and "
                f"MP3/WAV audio -- not {path.name}"
            )
            raise ProviderInputError(msg)
        most = settings.elyum.max_file_bytes
        if sent.stat().st_size > most:
            msg = f"{path.name} is over {PROVIDER}'s {most // 1_000_000} MB a file"
            raise ProviderInputError(msg)
        return sent

    def _upload(self, sent: Path) -> str:
        content_type = mimetypes.guess_type(sent.name)[0] or "application/octet-stream"
        with sent.open("rb") as fh:
            response = self._call(
                "POST", "files", files={"file": (sent.name, fh, content_type)}
            )
        try:
            return ElyumFile.model_validate_json(response.content).id
        except ValueError as exc:
            msg = f"{PROVIDER}: unreadable upload answer ({exc.__class__.__name__})"
            raise ProviderUnavailableError(msg) from exc

    # --- generations ------------------------------------------------------

    def create(self, body: dict[str, Any], idempotency_key: str) -> ElyumGeneration:
        generation = self._generation(
            self._call(
                "POST",
                "generations",
                json=body,
                headers={"Idempotency-Key": idempotency_header(idempotency_key)},
            )
        )
        logger.info(
            "elyum %s generation %s %s (job %s)",
            body.get("model"),
            generation.id,
            generation.status,
            idempotency_key,
        )
        return generation

    def get(self, generation_id: str) -> ElyumGeneration:
        generation = self._generation(self._call("GET", f"generations/{generation_id}"))
        if generation.status not in PENDING and generation.usage is not None:
            logger.info(
                "elyum generation %s %s: $%s %s",
                generation_id,
                generation.status,
                generation.usage.usd,
                generation.usage.billed,
            )
        return generation
