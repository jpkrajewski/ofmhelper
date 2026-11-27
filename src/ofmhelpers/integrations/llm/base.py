# Copyright (C) 2026 jpkrajewski

import importlib
import logging
import pkgutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar
lazy from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations.llm.errors import (
    LLMConfigError,
    LLMError,
    LLMInputError,
    LLMOutputError,
    LLMUnavailableError,
)
from ofmhelpers.integrations.llm.frames import contact_sheet
from ofmhelpers.integrations.llm.models import (
    LLMUse,
    MediaKind,
    MediaPart,
    Prompt,
    Reply,
    TextPart,
    Usage,
)

logger = logging.getLogger(__name__)

_MIN_REQUEST_S = 1.0


class Attempt[A: BaseModel](BaseModel):
    reply: Reply
    parsed: A


class Answered[A: BaseModel](BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: A
    text: str
    provider: str
    model: str
    attempts: int
    seconds: float
    input_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(ABC):
    name: ClassVar[str]
    label: ClassVar[str]
    media: ClassVar[frozenset[MediaKind]]
    max_images: ClassVar[int | None] = None
    temperature: ClassVar[float | None] = None

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    @classmethod
    @abstractmethod
    def default_key(cls) -> str | None: ...

    @classmethod
    def default_keys(cls) -> list[str]:
        key = cls.default_key()
        return [key] if key else []

    @classmethod
    @abstractmethod
    def default_model(cls) -> str: ...

    @abstractmethod
    def _ask(
        self,
        prompt: Prompt,
        answer: type[BaseModel],
        timeout_s: float,
    ) -> Reply: ...

    def ask[A: BaseModel](self, prompt: Prompt, answer: type[A]) -> A:
        return self.answered(prompt, answer).answer

    def answered[A: BaseModel](
        self,
        prompt: Prompt,
        answer: type[A],
        *,
        deadline: float | None = None,
    ) -> Answered[A]:
        started = time.monotonic()
        attempts = 0
        replies: list[Reply] = []

        def attempt(readable: Prompt, timeout_s: float) -> Attempt[A]:
            nonlocal attempts
            attempts += 1
            reply = self._ask(readable, answer, timeout_s)
            replies.append(reply)
            return Attempt(reply=reply, parsed=self._parsed(reply.text, answer))

        with tempfile.TemporaryDirectory(prefix="llm-") as work:
            readable = self._readable(prompt, Path(work))
            made = self._reasked(
                lambda: self._with_retries(lambda t: attempt(readable, t), deadline),
                deadline,
            )
        used = Usage.summed(r.usage for r in replies)
        return Answered(
            answer=made.parsed,
            text=made.reply.text,
            provider=self.label,
            model=self.model,
            attempts=attempts,
            seconds=round(time.monotonic() - started, 3),
            input_tokens=used.input_tokens,
            thinking_tokens=used.thinking_tokens,
            output_tokens=used.output_tokens,
        )

    def _temperature(self, prompt: Prompt) -> float | None:
        return self.temperature if prompt.temperature is None else prompt.temperature

    def _left_s(self, ends: float) -> float:
        left = ends - time.monotonic()
        if left < _MIN_REQUEST_S:
            raise self._too_long()
        return left

    def _too_long(self, last: LLMError | None = None) -> LLMUnavailableError:
        detail = ""
        if last is not None:
            detail = f"{last} | {last.detail}" if last.detail else str(last)
        return LLMUnavailableError(f"{self.label} took too long", detail)

    def _readable(self, prompt: Prompt, work: Path) -> Prompt:
        if MediaKind.VIDEO not in self.media and MediaKind.IMAGE in self.media:
            prompt = _videos_as_sheets(prompt, work)
        unread = {p.kind for p in prompt.media} - self.media
        if unread:
            kinds = ", ".join(sorted(unread))
            msg = f"{self.label} does not read {kinds}"
            raise LLMInputError(msg)
        images = sum(p.kind is MediaKind.IMAGE for p in prompt.media)
        if self.max_images is not None and images > self.max_images:
            msg = f"{self.label} takes at most {self.max_images} images, not {images}"
            raise LLMInputError(msg)
        return prompt

    def _reasked[T](self, attempt: Callable[[], T], deadline: float | None) -> T:
        tries = settings.llm.shape_retries
        for tried in range(tries + 1):
            try:
                return attempt()
            except LLMOutputError as exc:
                no_time = deadline is not None and (
                    deadline - time.monotonic() < _MIN_REQUEST_S
                )
                if tried == tries or no_time:
                    raise
                logger.warning(
                    "%s %s answered in the wrong shape (%s), asking again",
                    self.label,
                    self.model,
                    exc,
                    exc_info=True,
                )
        msg = "unreachable"
        raise AssertionError(msg)

    def _with_retries[T](
        self,
        attempt: Callable[[float], T],
        deadline: float | None,
    ) -> T:
        s = settings.llm
        for tried in range(s.retries + 1):
            timeout_s = (
                s.timeout_s
                if deadline is None
                else min(s.timeout_s, self._left_s(deadline))
            )
            try:
                return attempt(timeout_s)
            except LLMUnavailableError as exc:
                hint = exc.retry_after_s
                if tried == s.retries or (
                    hint is not None and hint > s.retry_max_wait_s
                ):
                    raise
                wait = min(
                    hint if hint is not None else s.retry_wait_s * 2**tried,
                    s.retry_max_wait_s,
                )
                if deadline is not None and (
                    time.monotonic() + wait + _MIN_REQUEST_S > deadline
                ):
                    raise self._too_long(exc) from exc
                logger.warning(
                    "%s %s unavailable (%s), retry %d in %.0f s",
                    self.label,
                    self.model,
                    exc,
                    tried + 1,
                    wait,
                    exc_info=True,
                )
                time.sleep(wait)
        msg = "unreachable"
        raise AssertionError(msg)

    def _parsed[A: BaseModel](self, text: str, answer: type[A]) -> A:
        try:
            return answer.model_validate_json(text)
        except ValidationError as exc:
            msg = f"{self.label} answered in the wrong shape"
            raise LLMOutputError(msg, detail=f"{exc}\n{text[:2000]}") from exc


def _videos_as_sheets(prompt: Prompt, work: Path) -> Prompt:
    s = settings.llm
    parts: list[TextPart | MediaPart] = []
    for i, part in enumerate(prompt.parts):
        if not (isinstance(part, MediaPart) and part.kind is MediaKind.VIDEO):
            parts.append(part)
            continue
        try:
            sheet = contact_sheet(
                part.path,
                work / f"sheet-{i}.jpg",
                s.video_sheet_frames,
                s.video_sheet_frame_px,
                s.video_sheet_timeout_s,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            msg = f"Could not read the video {part.path.name}"
            raise LLMInputError(msg, detail=str(exc)) from exc
        note = (
            f"The next image is the video {part.path.name} as"
            f" {s.video_sheet_frames} evenly spaced stills in time order,"
            " left to right, top to bottom."
        )
        parts += [TextPart(text=note), MediaPart.of(sheet)]
    return prompt.model_copy(update={"parts": parts})


class _Registered(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    classes: dict[str, type[LLMProvider]] = Field(default_factory=dict)


@lru_cache(maxsize=1)
def _registered() -> _Registered:
    return _Registered()


def register_llm[P: type[LLMProvider]](cls: P) -> P:
    _registered().classes[cls.name] = cls
    return cls


@lru_cache(maxsize=1)
def _import_providers() -> None:
    package = importlib.import_module("ofmhelpers.integrations.llm")
    for module in pkgutil.iter_modules(package.__path__):
        if module.ispkg:
            try:
                importlib.import_module(f"{package.__name__}.{module.name}.provider")
            except ModuleNotFoundError as exc:
                if not (exc.name or "").endswith(".provider"):
                    raise


def llm_classes() -> MappingProxyType[str, type[LLMProvider]]:
    _import_providers()
    return MappingProxyType(dict(sorted(_registered().classes.items())))


def llm_names() -> list[str]:
    return list(llm_classes())


def llm_class(name: str) -> type[LLMProvider]:
    try:
        return llm_classes()[name]
    except KeyError:
        msg = f"Unknown language-model provider: {name}"
        raise LLMConfigError(msg) from None


def default_for(use: LLMUse) -> str:
    s = settings.llm
    return s.default_video if use is LLMUse.VIDEO else s.default_chat


def get_llm(
    use: LLMUse = LLMUse.CHAT,
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    name, _, named_model = (provider or default_for(use)).partition(":")
    cls = llm_class(name.strip().lower())
    key = api_key or cls.default_key()
    if not key:
        msg = f"{cls.label}: no API key configured"
        raise LLMConfigError(msg)
    return cls(key, model or named_model.strip() or cls.default_model())
