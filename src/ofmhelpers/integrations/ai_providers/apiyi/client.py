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
    data: list[ListedModel] = Field(default_factory=list)


LOCAL = "file://"
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov"})
AUDIO_SUFFIXES = frozenset({".wav", ".mp3"})
ACCEPTED_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES


class InlineImage(BaseModel):
    model_config = ConfigDict(frozen=True)

    mime: str
    data: str


def refused(response: requests.Response) -> NoReturn:
    raise_for(response.status_code, response.text[:2000])


def refused_read(response: requests.Response) -> NoReturn:
    raise_for_read(response.status_code, response.text[:2000])


def refused_moderated(response: requests.Response) -> NoReturn:
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
    return Path(ref.removeprefix(LOCAL)) if ref.startswith(LOCAL) else None


def maybe_paid(exc: ProviderUnavailableError) -> ProviderUnavailableError:
    if isinstance(exc, ProviderRateLimitError) or not exc.maybe_made:
        return exc
    msg = (
        f"{PROVIDER} did not answer the submit ({exc}). It may be made and "
        "paid, so it is not sent again: check APIYI's log before running it "
        "again."
    )
    return ProviderUnavailableError(msg)


class ApiyiClient:
    def __init__(self, api_key: str, *, lighter: bool = False) -> None:
        self.api_key = api_key
        self.lighter = lighter
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
        try:
            return self.call("POST", path, **kwargs)
        except ProviderUnavailableError as exc:
            raise maybe_paid(exc) from exc

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        self.check_key()
        return f"{LOCAL}{self._sendable(path, max_bytes).resolve()}"

    def check_key(self) -> None:
        self.model_ids()

    def model_ids(self) -> list[str]:
        listed = memoized(
            upload_cache_key("apiyi-key", self.api_key, "model-ids"),
            settings.apiyi.key_check_ttl_s,
            self._model_ids,
        )
        return listed.split()

    def _model_ids(self) -> str:
        answer = parsed(
            self.call("GET", MODELS_PATH), ModelList, PROVIDER, "model list"
        )
        return " ".join(model.id for model in answer.data if model.id)

    @staticmethod
    def _sendable(path: Path, max_bytes: int | None) -> Path:
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

    def link(self, ref: str) -> str:
        path = local_path(ref)
        if path is None:
            return ref
        s = settings.apiyi
        if self.lighter:
            limits = ImageLimits(
                max_bytes=s.link_image_max_bytes, max_side=s.ref_image_max_side
            )
            out = Path(settings.aigen.out_dir) / "apiyi" / "fitted"
            try:
                path = fitted(path, out, limits)
            except (OSError, ValueError) as exc:
                logger.warning("apiyi: %s could not be prepared", path, exc_info=True)
                msg = f"Could not prepare {path.name} for {PROVIDER}"
                raise ProviderInputError(msg) from exc
        return hosting.link(path, PROVIDER, s.upload_cache_ttl_s)

    @staticmethod
    def image_file(ref: str, limits: ImageLimits) -> Path:
        path = local_path(ref)
        if path is None:
            msg = f"{PROVIDER}: {ref} is not a file on this server"
            raise ProviderInputError(msg)
        out = Path(settings.aigen.out_dir) / "apiyi" / "inline"
        return fitted(path, out, limits)

    @classmethod
    def inline(cls, ref: str, limits: ImageLimits) -> InlineImage:
        path = cls.image_file(ref, limits)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return InlineImage(mime=mime, data=base64.b64encode(path.read_bytes()).decode())

    @classmethod
    def data_url(cls, ref: str, limits: ImageLimits) -> str:
        image = cls.inline(ref, limits)
        return f"data:{image.mime};base64,{image.data}"

    @staticmethod
    def fetch(url: str) -> bytes:
        try:
            response = outbound.get(url, timeout=settings.aigen.download_timeout_s)
            response.raise_for_status()
        except requests.RequestException as exc:
            msg = f"{PROVIDER}: could not fetch the result {url.split('?', maxsplit=1)[0]}"
            raise ProviderUnavailableError(msg) from exc
        return response.content
