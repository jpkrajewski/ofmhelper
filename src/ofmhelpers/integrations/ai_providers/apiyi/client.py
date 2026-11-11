"""APIYI's gateway (api.apiyi.com), shared by every family (`families/`): one
key's HTTP session, its key check, and the reference files a request takes.
What each API sends and reads is its family's.

- No idempotency key on any of its APIs, no task list, no callback: a submit
  is sent once, never retried here (`self_heal` resends only a rate-limit
  refusal); a lost answer is reported as maybe paid.
- No upload endpoint: `upload` checks a file against what APIYI's models take
  and answers a local reference (`file://<path>`); each family turns it into
  what its API takes at submit -- a link to this server (`link`: DashScope,
  Seedance), inline base64 (`inline`: Gemini, Seedream, FLUX) or the file
  itself (multipart: GPT-Image, Veo). So image models need no public address.
  The key is checked once first (`GET /v1/models`), so a wrong one fails
  before anything is paid.

Through `integrations.outbound`, so OFM_FAKE_EXTERNALS answers it from `fake.py`.
"""

import base64
import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

import requests
from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.ai_providers import hosting
from ofmhelpers.integrations.ai_providers.apiyi.models import (
    PROVIDER,
    raise_for,
    raise_for_read,
)
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderInputError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.ai_providers.http import (
    Lenient,
    bearer_session,
    call,
    parsed,
)
from ofmhelpers.integrations.ai_providers.uploads import (
    memoized,
    reference_copy,
    upload_cache_key,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media.image_fit import ImageLimits, fitted

logger = get_logger(__name__)

MODELS_PATH = "/v1/models"


class ListedModel(Lenient):
    id: str = ""


class ModelList(Lenient):
    """`GET /v1/models`: the models the asking key's token group reaches."""

    data: list[ListedModel] = Field(default_factory=list)


LOCAL = "file://"
# What APIYI's models take between them (each family checks its own).
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov"})
AUDIO_SUFFIXES = frozenset({".wav", ".mp3"})
ACCEPTED_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES


class InlineImage(BaseModel):
    """An image sent inside a request: its mime type and base64 bytes."""

    model_config = ConfigDict(frozen=True)

    mime: str
    data: str


def refused(response: requests.Response) -> NoReturn:
    raise_for(response.status_code, response.text[:2000])


def refused_read(response: requests.Response) -> NoReturn:
    raise_for_read(response.status_code, response.text[:2000])


def refused_moderated(response: requests.Response) -> NoReturn:
    """A submit where a 403 means moderation, not the key (Seedance)."""
    raise_for(response.status_code, response.text[:2000], forbidden_is_refusal=True)


def _takes(path: Path) -> bool:
    return path.suffix.lower() in ACCEPTED_SUFFIXES


def _most_bytes(path: Path) -> int:
    s = settings.apiyi
    suffix = path.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return s.max_video_bytes
    if suffix in AUDIO_SUFFIXES:
        return s.max_audio_bytes
    return s.ref_image_max_bytes


def local_path(ref: str) -> Path | None:
    """The file a reference names, or None for a URL."""
    return Path(ref.removeprefix(LOCAL)) if ref.startswith(LOCAL) else None


def maybe_paid(exc: ProviderUnavailableError) -> ProviderUnavailableError:
    """A submit whose answer never came, worded so nobody runs it again
    blindly: no APIYI API has an idempotency key or a task list."""
    if isinstance(exc, ProviderRateLimitError) or not exc.maybe_made:
        return exc
    msg = (
        f"{PROVIDER} did not answer the submit ({exc}). It may be made and "
        "paid, so it is not sent again: check APIYI's log before running it "
        "again."
    )
    return ProviderUnavailableError(msg)


class ApiyiClient:
    """One APIYI key."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._base = settings.apiyi.base_url.rstrip("/")
        self._http = bearer_session(api_key)

    def call(
        self,
        method: str,
        path: str,
        refuse: Callable[[requests.Response], NoReturn] = refused,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> requests.Response:
        """One request to the gateway; HTTP errors as the shared errors."""
        return call(
            self._http,
            PROVIDER,
            refuse,
            method,
            f"{self._base}{path}",
            timeout=timeout or settings.apiyi.request_timeout_s,
            **kwargs,
        )

    def submit(self, path: str, **kwargs: Any) -> requests.Response:
        """A request that may start paid work: a lost answer is reported as
        maybe paid (never resent)."""
        try:
            return self.call("POST", path, **kwargs)
        except ProviderUnavailableError as exc:
            raise maybe_paid(exc) from exc

    # --- references ---------------------------------------------------------

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        """A reference to `path` (or the copy of it APIYI's models take) for a
        family to send at submit; the key checked first."""
        self.check_key()
        return f"{LOCAL}{self._sendable(path, max_bytes).resolve()}"

    def check_key(self) -> None:
        """`GET /v1/models` once per key per `key_check_ttl_s`: a wrong key is
        `ProviderAuthError` here, before a reference is shared or paid for."""
        self.model_ids()

    def model_ids(self) -> list[str]:
        """The models this key reaches (its token group's), asked once per key
        per `key_check_ttl_s`; a wrong key is `ProviderAuthError`."""
        listed = memoized(
            upload_cache_key("apiyi-key", self.api_key, "model-ids"),
            settings.apiyi.key_check_ttl_s,
            self._model_ids,
        )
        return listed.split()

    def _model_ids(self) -> str:
        """Its model ids as one line (what `memoized` keeps); "" for none."""
        answer = parsed(
            self.call("GET", MODELS_PATH), ModelList, PROVIDER, "model list"
        )
        return " ".join(model.id for model in answer.data if model.id)

    @staticmethod
    def _sendable(path: Path, max_bytes: int | None) -> Path:
        """`path`, or the copy of it APIYI's models take: a .webm/.mkv/.m4v as
        MP4, a TIFF or an image over Wan's limits as JPEG. Refused here,
        before any call, when no copy fits."""
        s = settings.apiyi
        try:
            sent = reference_copy(
                path,
                "apiyi",
                s.ref_image_limits.with_max_bytes(max_bytes),
                s.video_convert_timeout_s,
                _takes,
            )
        except (RuntimeError, OSError, ValueError) as exc:
            logger.warning("apiyi: %s could not be prepared", path, exc_info=True)
            msg = f"Could not prepare {path.name} for {PROVIDER}"
            raise ProviderInputError(msg) from exc
        if not _takes(sent):
            msg = (
                f"{PROVIDER} takes JPEG/PNG/BMP/WebP images, MP4/MOV video and "
                f"WAV/MP3 audio -- not {path.name}"
            )
            raise ProviderInputError(msg)
        most = max(_most_bytes(sent), max_bytes or 0)
        if sent.stat().st_size > most:
            msg = f"{path.name} is over {PROVIDER}'s {most // 1_000_000} MB a file"
            raise ProviderInputError(msg)
        return sent

    @staticmethod
    def link(ref: str) -> str:
        """A public link the API fetches the reference from (a URL as is)."""
        path = local_path(ref)
        if path is None:
            return ref
        return hosting.link(path, PROVIDER, settings.apiyi.upload_cache_ttl_s)

    @staticmethod
    def image_file(ref: str, limits: ImageLimits) -> Path:
        """The local image a reference names, within `limits` (a JPEG copy
        when over them)."""
        path = local_path(ref)
        if path is None:
            msg = f"{PROVIDER}: {ref} is not a file on this server"
            raise ProviderInputError(msg)
        out = Path(settings.aigen.out_dir) / "apiyi" / "inline"
        return fitted(path, out, limits)

    @classmethod
    def inline(cls, ref: str, limits: ImageLimits) -> InlineImage:
        """A reference image within `limits`, as base64."""
        path = cls.image_file(ref, limits)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return InlineImage(mime=mime, data=base64.b64encode(path.read_bytes()).decode())

    @classmethod
    def data_url(cls, ref: str, limits: ImageLimits) -> str:
        image = cls.inline(ref, limits)
        return f"data:{image.mime};base64,{image.data}"

    @staticmethod
    def fetch(url: str) -> bytes:
        """A result URL's bytes, fetched without our key (signed OSS/TOS/CDN
        links answer 403 to one)."""
        try:
            response = outbound.get(url, timeout=settings.aigen.download_timeout_s)
            response.raise_for_status()
        except requests.RequestException as exc:
            msg = f"{PROVIDER}: could not fetch the result {url.split('?', maxsplit=1)[0]}"
            raise ProviderUnavailableError(msg) from exc
        return response.content
