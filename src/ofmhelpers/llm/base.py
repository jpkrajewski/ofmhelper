"""The one interface every language-model provider implements, and how
providers are found.

A provider is a subpackage of `llm` with a `provider.py` defining one
`LLMProvider` subclass decorated `@register_llm`. Nothing else in the app
names a provider: callers ask `get_llm(LLMUse.VIDEO)` or
`get_llm(LLMUse.CHAT)` -- the configured default for that kind of work
(`LLM_DEFAULT_VIDEO`, `LLM_DEFAULT_CHAT`) -- so adding one (OpenAI,
Anthropic, a local model, ...) is that subpackage and its key/model fields
in `LLMSettings`, nothing more.

One call: `ask(prompt, Answer)` sends a `Prompt` of text and media parts and
returns an instance of the Pydantic model `Answer`, the model having been
told to answer in exactly that shape (its JSON schema). Written once, here:
a video for a provider that reads images only becomes a contact sheet
(`frames.py`); media it cannot take is refused before anything is sent;
rate limits and outages are retried, never past the caller's deadline
(`answered(..., deadline=)`); the answer is validated. A provider writes
only `_ask`, one attempt within the timeout it is given.
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.llm.errors import (
    LLMConfigError,
    LLMError,
    LLMInputError,
    LLMOutputError,
    LLMUnavailableError,
)
from ofmhelpers.llm.frames import contact_sheet
from ofmhelpers.llm.models import (
    LLMUse,
    MediaKind,
    MediaPart,
    Prompt,
    Reply,
    TextPart,
    Usage,
)
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

# A request left less time than this before the deadline could only time
# out: the call ends there instead.
_MIN_REQUEST_S = 1.0


class Answered[A: BaseModel](BaseModel):
    """An answer and how it came: the model's raw text, who answered, the
    attempts (retries included) and the seconds it took."""

    model_config = ConfigDict(frozen=True)

    answer: A
    text: str
    provider: str
    model: str
    attempts: int
    seconds: float
    # The tokens it took, every attempt that got an answer added up (an
    # answer in the wrong shape, asked again, is billed too); None: the
    # provider did not say.
    input_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(ABC):
    """One account on one provider and one of its models. Built per use:
    cheap, holds no connection between calls."""

    name: ClassVar[str]
    label: ClassVar[str]
    # The media kinds its models read; a prompt with any other is refused
    # before anything is sent (a video, for one that reads images, is sent
    # as a contact sheet instead).
    media: ClassVar[frozenset[MediaKind]]
    # Images one request may carry, when the provider caps it.
    max_images: ClassVar[int | None] = None
    # Sent when the prompt names no temperature; None: none is sent (the
    # model's own default).
    temperature: ClassVar[float | None] = None

    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    @classmethod
    @abstractmethod
    def default_key(cls) -> str | None:
        """The configured key (.env), if any."""

    @classmethod
    def default_keys(cls) -> list[str]:
        """Every configured key, in the order to try them (a provider may
        hold several projects' keys); `default_key` first."""
        key = cls.default_key()
        return [key] if key else []

    @classmethod
    @abstractmethod
    def default_model(cls) -> str:
        """The configured model id."""

    @abstractmethod
    def _ask(self, prompt: Prompt, answer: type[BaseModel], timeout_s: float) -> Reply:
        """One attempt, over within `timeout_s`: the model's answer as JSON
        text, told to follow `answer`'s schema, and the tokens it took.
        Raises the `llm.errors` family only."""

    def ask[A: BaseModel](self, prompt: Prompt, answer: type[A]) -> A:
        """`prompt` answered as an `answer`. Rate limits and outages are
        retried (`settings.llm.retries`); raises `LLMError`s."""
        return self.answered(prompt, answer).answer

    def answered[A: BaseModel](
        self, prompt: Prompt, answer: type[A], *, deadline: float | None = None
    ) -> Answered[A]:
        """`ask`, with what a debugger wants next to the answer: the raw
        text, who answered, how many attempts, how long it took and the
        tokens it used. `deadline` (a `time.monotonic()` value): nothing
        runs past it -- each request's timeout is cut to the time left, and
        a retry or a wait it would cut short raises `LLMUnavailableError`
        ("<label> took too long") instead."""
        started = time.monotonic()
        attempts = 0
        replies: list[Reply] = []

        def attempt(readable: Prompt, timeout_s: float) -> tuple[Reply, A]:
            nonlocal attempts
            attempts += 1
            reply = self._ask(readable, answer, timeout_s)
            replies.append(reply)
            return reply, self._parsed(reply.text, answer)

        with tempfile.TemporaryDirectory(prefix="llm-") as work:
            readable = self._readable(prompt, Path(work))
            reply, parsed = self._reasked(
                lambda: self._with_retries(lambda t: attempt(readable, t), deadline),
                deadline,
            )
        used = Usage.summed(r.usage for r in replies)
        return Answered(
            answer=parsed,
            text=reply.text,
            provider=self.label,
            model=self.model,
            attempts=attempts,
            seconds=round(time.monotonic() - started, 3),
            input_tokens=used.input_tokens,
            thinking_tokens=used.thinking_tokens,
            output_tokens=used.output_tokens,
        )

    def _temperature(self, prompt: Prompt) -> float | None:
        """The prompt's temperature, else this provider's default."""
        return self.temperature if prompt.temperature is None else prompt.temperature

    def _left_s(self, ends: float) -> float:
        """Seconds until `ends` (a `time.monotonic()` value); fewer than a
        request needs raise `LLMUnavailableError` ("took too long")."""
        left = ends - time.monotonic()
        if left < _MIN_REQUEST_S:
            raise self._too_long()
        return left

    def _too_long(self, last: LLMError | None = None) -> LLMUnavailableError:
        """The call cut short by its deadline; `last`: the failure it would
        have tried again after."""
        detail = ""
        if last is not None:
            detail = f"{last} | {last.detail}" if last.detail else str(last)
        return LLMUnavailableError(f"{self.label} took too long", detail)

    def _readable(self, prompt: Prompt, work: Path) -> Prompt:
        """`prompt` as this provider can take it, or `LLMInputError`."""
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
        """`attempt`, asked again up to `settings.llm.shape_retries` times
        when the answer is in the wrong shape: a model's JSON mode now and
        then misses (Groq `json_validate_failed` on a AI check, dev
        2026-10-25), and the same question asked again usually lands. Not
        once `deadline` leaves no time for another request."""
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
                )
        msg = "unreachable"  # the loop returns or raises
        raise AssertionError(msg)

    def _with_retries[T](
        self, attempt: Callable[[float], T], deadline: float | None
    ) -> T:
        """`attempt(timeout_s)`, tried again after `LLMUnavailableError`
        (`settings.llm.retries` times, waiting the provider's hint, else
        `retry_wait_s` doubling), each request's timeout `timeout_s` cut to
        the time left before `deadline`; a wait that would run past it is
        not waited: "took too long"."""
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
                )
                time.sleep(wait)
        msg = "unreachable"  # the loop returns or raises
        raise AssertionError(msg)

    def _parsed[A: BaseModel](self, text: str, answer: type[A]) -> A:
        try:
            return answer.model_validate_json(text)
        except ValidationError as exc:
            msg = f"{self.label} answered in the wrong shape"
            raise LLMOutputError(msg, detail=f"{exc}\n{text[:2000]}") from exc


def _videos_as_sheets(prompt: Prompt, work: Path) -> Prompt:
    """Every video in `prompt` replaced by a note and its contact sheet."""
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


@lru_cache(maxsize=1)
def _registry() -> dict[str, type[LLMProvider]]:
    """Every registered provider by name (a singleton accessor, not module
    state)."""
    return {}


def register_llm[P: type[LLMProvider]](cls: P) -> P:
    """Class decorator: make `cls` a provider the app can ask."""
    _registry()[cls.name] = cls
    return cls


@lru_cache(maxsize=1)
def _import_providers() -> None:
    """Import every subpackage's `provider` module once, which registers
    its provider."""
    package = importlib.import_module("ofmhelpers.llm")
    for module in pkgutil.iter_modules(package.__path__):
        if module.ispkg:
            try:
                importlib.import_module(f"{package.__name__}.{module.name}.provider")
            except ModuleNotFoundError as exc:
                if not (exc.name or "").endswith(".provider"):
                    raise


def llm_classes() -> dict[str, type[LLMProvider]]:
    _import_providers()
    return dict(sorted(_registry().items()))


def llm_names() -> list[str]:
    return list(llm_classes())


def llm_class(name: str) -> type[LLMProvider]:
    try:
        return llm_classes()[name]
    except KeyError:
        msg = f"Unknown language-model provider: {name}"
        raise LLMConfigError(msg) from None


def default_for(use: LLMUse) -> str:
    """The configured `<provider>[:<model>]` for `use`."""
    s = settings.llm
    return s.default_video if use is LLMUse.VIDEO else s.default_chat


def get_llm(
    use: LLMUse = LLMUse.CHAT,
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> LLMProvider:
    """A provider ready to `ask`: the configured default for `use`, or
    `provider` (`<name>[:<model>]`) when given; `api_key` and `model`
    default to the configured ones. Raises `LLMConfigError` for an unknown
    provider or no key."""
    name, _, named_model = (provider or default_for(use)).partition(":")
    cls = llm_class(name.strip().lower())
    key = api_key or cls.default_key()
    if not key:
        msg = f"{cls.label}: no API key configured"
        raise LLMConfigError(msg)
    return cls(key, model or named_model.strip() or cls.default_model())
