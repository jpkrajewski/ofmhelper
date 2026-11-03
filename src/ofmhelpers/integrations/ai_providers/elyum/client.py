"""Elyum's public API (elyum.ai/open/docs): upload a reference file, start a
generation, read it back. Single calls only; waiting, resuming and
downloading are `runner.py`'s.

Every submit carries our idempotency key as `Idempotency-Key`: a retry of
the same body returns the generation already made (`Idempotent-Replayed`),
never a second paid one. Through `utils.outbound`, so OFM_FAKE_EXTERNALS
answers it from `fake.py`.
"""

import mimetypes
import re
from pathlib import Path
from typing import Any, NoReturn

import requests

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.elyum.models import (
    PENDING,
    PROVIDER,
    ElyumFile,
    ElyumGeneration,
    raise_for,
)
from ofmhelpers.integrations.ai_providers.errors import ProviderInputError
from ofmhelpers.integrations.ai_providers.http import bearer_session, call, parsed
from ofmhelpers.integrations.ai_providers.uploads import (
    memoized,
    reference_copy,
    upload_cache_key,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

API = "/v1"
# What POST /v1/files takes (PNG, JPG, WebP, MP4, MOV, WebM, MP3, WAV).
ACCEPTED_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm", ".mp3", ".wav"}
)
# Idempotency-Key: up to 255 printable characters, no spaces.
_KEY_UNSAFE = re.compile(r"[^\x21-\x7e]")
_KEY_MAX = 255


def idempotency_header(key: str) -> str:
    return _KEY_UNSAFE.sub("_", key)[:_KEY_MAX]


def _refused(response: requests.Response) -> NoReturn:
    raise_for(response.status_code, response.text[:2000])


def _takes(path: Path) -> bool:
    return path.suffix.lower() in ACCEPTED_SUFFIXES


class ElyumClient:
    """One Elyum account (its API key)."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._base = settings.elyum.base_url.rstrip("/") + API
        self._http = bearer_session(api_key)

    def _call(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        return call(
            self._http,
            PROVIDER,
            _refused,
            method,
            f"{self._base}/{path}",
            timeout=settings.elyum.request_timeout_s,
            **kwargs,
        )

    # --- upload -----------------------------------------------------------

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        """The `file_...` id of `path` on this account; one upload per file
        across workers, reused while cached."""
        return memoized(
            upload_cache_key("elyum", self.api_key, path, max_bytes),
            settings.elyum.upload_cache_ttl_s,
            lambda: self._upload(path, max_bytes),
        )

    @staticmethod
    def _sendable(path: Path, max_bytes: int | None) -> Path:
        """`path`, or the copy of it Elyum takes: an .mkv/.m4v as MP4, a
        BMP/TIFF or an image over the limits as JPEG. Refused here, before
        any call, when no copy fits."""
        s = settings.elyum
        try:
            sent = reference_copy(
                path,
                "elyum",
                s.ref_image_limits.with_max_bytes(max_bytes),
                s.video_convert_timeout_s,
                _takes,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            logger.warning("elyum: %s could not be prepared", path, exc_info=True)
            msg = f"Could not prepare {path.name} for {PROVIDER}"
            raise ProviderInputError(msg) from exc
        if not _takes(sent):
            msg = (
                f"{PROVIDER} takes PNG/JPG/WebP images, MP4/MOV/WebM video and "
                f"MP3/WAV audio -- not {path.name}"
            )
            raise ProviderInputError(msg)
        most = s.max_file_bytes
        if sent.stat().st_size > most:
            msg = f"{path.name} is over {PROVIDER}'s {most // 1_000_000} MB a file"
            raise ProviderInputError(msg)
        return sent

    def _upload(self, path: Path, max_bytes: int | None) -> str:
        sent = self._sendable(path, max_bytes)
        content_type = mimetypes.guess_type(sent.name)[0] or "application/octet-stream"
        with sent.open("rb") as fh:
            response = self._call(
                "POST", "files", files={"file": (sent.name, fh, content_type)}
            )
        file_id = parsed(response, ElyumFile, PROVIDER, "upload answer").id
        logger.info("elyum upload %s -> %s", path.name, file_id)
        return file_id

    # --- generations ------------------------------------------------------

    def create(self, body: dict[str, Any], idempotency_key: str) -> ElyumGeneration:
        response = self._call(
            "POST",
            "generations",
            json=body,
            headers={"Idempotency-Key": idempotency_header(idempotency_key)},
        )
        generation = parsed(response, ElyumGeneration, PROVIDER)
        logger.info(
            "elyum %s generation %s %s (job %s)",
            body.get("model"),
            generation.id,
            generation.status,
            idempotency_key,
        )
        return generation

    def get(self, generation_id: str) -> ElyumGeneration:
        response = self._call("GET", f"generations/{generation_id}")
        generation = parsed(response, ElyumGeneration, PROVIDER)
        if generation.status not in PENDING and generation.usage is not None:
            logger.info(
                "elyum generation %s %s: $%s %s",
                generation_id,
                generation.status,
                generation.usage.usd,
                generation.usage.billed,
            )
        return generation
