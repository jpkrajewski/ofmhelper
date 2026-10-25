"""Is a generated reel too glitchy to post?

The whole video becomes one contact sheet (`llm.frames`: a still every
`settings.clone_ai_check.every_s`, first and last frame included, each labelled
with its time), which goes with `RULES` to the video models
(`settings.llm.video_chain`: `LLM_DEFAULT_VIDEO`, then each of
`LLM_FALLBACK_VIDEO` while the one before is down or out of quota). The
model answers a `CloneAiVerdict`: its reasoning, every glitch with its time
and severity, and how sure it is (0-100); GOOD or GLITCHY follows from those
(`CloneAiVerdict.verdict`).

Never raises for what can go wrong with a video or a model: an unreadable
video or an `LLMError` comes back as a `CloneAiCheck` with no verdict, a
short `error` safe to show, its `error_kind` and the raw text in
`error_detail`, so the caller keeps the failure like any result. A whole
check asks no model after `settings.clone_ai_check.deadline_s`. The sheet
lives in a temporary folder for the call only; the stored still times are
enough to draw it again (`sheet`).
"""

import math
import subprocess
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ofmhelpers.clone_ai_check.models import CloneAiCheck, CloneAiVerdict, ErrorKind
from ofmhelpers.config import settings
from ofmhelpers.llm import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMInputError,
    LLMOutputError,
    LLMQuotaError,
    LLMRefusedError,
    LLMUnavailableError,
    LLMUse,
    MediaKind,
    Prompt,
    get_llm,
    llm_class,
)
from ofmhelpers.llm.base import Answered
from ofmhelpers.llm.frames import contact_sheet, still_times, video_seconds
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

RULES = """\
You check short AI-generated videos (Instagram reels) before they are posted.
You get a contact sheet of one whole video: stills from its first frame to its
last, in time order (left to right, top to bottom), each labelled with its time.

Look for anything that gives the video away as AI-made or broken:
- faces melting, warping or turning into someone else
- bodies: extra or missing limbs, fingers or heads; joints bending the wrong
  way; parts merging; missing features (no belly button, no nipples)
- clothes: changing, morphing, appearing or vanishing between stills; fabric
  fused with skin; clothes that make no sense on this person (a man in a
  strapless tube top, a top missing the straps it needs, one sleeve only)
- garbled or nonsense text and logos on clothes or objects
- objects or background appearing out of nowhere, smeared or dissolving

Ordinary motion blur, lighting changes, camera moves and cuts are fine.

List every problem. Mark one "blocking" when it alone would show a viewer
the reel is AI-made or broken, "minor" when it is easy to miss.
The percent is how sure you are the whole reel should not be posted: any
blocking problem makes it high, and several minor ones can add up to high.
Keep it short: a few words per problem, one sentence of reasoning."""

MIRRORED = (
    " The video was mirrored left to right on purpose: text and logos reading"
    " backwards are not a glitch."
)

# What a check without a verdict says (`CloneAiCheck.error`, shown on the
# card): a few fixed words; the raw text goes to `error_detail` and the log.
UNREADABLE = "Could not read the video"
NO_SHEET = "Could not make the contact sheet"
TOO_LONG = "AI check took too long"
NO_ANSWER = "No AI model answered"
NO_MODEL = "No AI model set up to check videos"
_REASONS: tuple[tuple[type[LLMError], str], ...] = (
    (LLMOutputError, "The AI gave a broken answer"),
    (LLMRefusedError, "The AI refused to check this video"),
    (LLMAuthError, "The AI key was rejected"),
    (LLMInputError, "The AI could not take this video"),
)
FAILED = "The AI check failed"

_ERRORS = (OSError, RuntimeError, ValueError, subprocess.SubprocessError)


class _NoAnswer(BaseModel):
    """Why no model along the chain answered: `error` (its class is the
    kind of failure), `reason` (what the card says), what each entry said,
    the last one asked."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    error: LLMError
    reason: str
    said: list[str]
    provider: str = ""
    model: str = ""


def still_count(seconds: float) -> int:
    """Stills for a video `seconds` long: one per `every_s`, both ends
    included, between 2 and `max_frames`."""
    s = settings.clone_ai_check
    return max(2, min(s.max_frames, math.floor(seconds / s.every_s) + 1))


def prompt_text(
    video: Path, seconds: float, stills: int, *, mirrored: bool = False
) -> str:
    return (
        f"Contact sheet of {video.name} ({seconds:.1f} s, {stills} stills)."
        + (MIRRORED if mirrored else "")
        + " Is it too glitchy to post?"
    )


def unanswered(
    video: Path, error: str, kind: ErrorKind = "failed", *, detail: str | None = None
) -> CloneAiCheck:
    """A check of `video` that got no verdict, for a caller whose check
    could not run at all (`error`: a few words, shown on the card;
    `detail`: the raw text, never shown)."""
    s = settings.clone_ai_check
    return CloneAiCheck(
        video=str(video),
        frame_px=s.frame_px,
        prompt_system=RULES,
        prompt_text="",
        error=error,
        error_kind=kind,
        error_detail=detail,
    )


def check_clone(video: Path, *, mirrored: bool = False) -> CloneAiCheck:
    """The reel at `video` checked. See the module docstring. `mirrored`:
    the video was flipped left-right on export (its text reads backwards),
    which the model is told."""
    s = settings.clone_ai_check
    started = time.monotonic()
    deadline = started + s.deadline_s
    found = CloneAiCheck(
        video=str(video), frame_px=s.frame_px, prompt_system=RULES, prompt_text=""
    )
    try:
        seconds = video_seconds(video, s.ffmpeg_timeout_s)
    except _ERRORS as exc:
        logger.warning("AI check: cannot read %s", video, exc_info=True)
        return _failed(found, UNREADABLE, "failed", str(exc))
    count = still_count(seconds)
    text = prompt_text(video, seconds, count, mirrored=mirrored)
    found = found.model_copy(update={"video_seconds": seconds, "prompt_text": text})
    with tempfile.TemporaryDirectory(prefix="clone_ai_check-") as work:
        try:
            sheet = sheet_at(video, Path(work) / "sheet.jpg", count)
            prompt = Prompt.of(text, sheet, system=RULES)
        except (*_ERRORS, LLMInputError) as exc:
            logger.warning("AI check: no contact sheet of %s", video, exc_info=True)
            return _failed(found, NO_SHEET, "failed", str(exc))
        found = found.model_copy(
            update={
                "stills_at": still_times(seconds, count),
                "sheet_s": round(time.monotonic() - started, 3),
            }
        )
        asked_at = time.monotonic()
        asked = _ask(prompt, deadline)
    llm_s = round(time.monotonic() - asked_at, 3)
    if isinstance(asked, _NoAnswer):
        return _unanswered_check(found, asked, llm_s)
    verdict = asked.answer
    return found.model_copy(
        update={
            "verdict": verdict.verdict(s.glitchy_percent),
            "glitch_percent": verdict.glitch_percent,
            "glitches": verdict.glitches,
            "reasoning": verdict.reasoning,
            "provider": asked.provider,
            "model": asked.model,
            "raw_answer": asked.text,
            "attempts": asked.attempts,
            "llm_s": asked.seconds,
            "input_tokens": asked.input_tokens,
            "thinking_tokens": asked.thinking_tokens,
            "output_tokens": asked.output_tokens,
        }
    )


def _ask(prompt: Prompt, deadline: float) -> Answered[CloneAiVerdict] | _NoAnswer:
    """The first answer along `settings.llm.video_chain`, asking no model
    once `deadline` (a `time.monotonic()` value) has passed. An entry whose
    provider cannot read video is skipped: Groq/Cerebras get the sheet shrunk
    and said GOOD to every reel (llm/CLAUDE.md findings). A provider down,
    rate limited, out of quota or not set up hands over to the next; any
    other failure (refused, a wrong answer) is the answer. A provider with
    several keys (Gemini: one per project, each its own free quota) tries
    every key on a model -- out of quota or rejected = the next key -- before
    the next model (user ask 2026-10-25); an overloaded model or an outage
    goes straight to the next model, since every key would wait on it."""
    said: list[str] = []
    provider = model = ""
    unavailable = False

    def none_answered(error: LLMError, reason: str) -> _NoAnswer:
        return _NoAnswer(
            error=error, reason=reason, said=said, provider=provider, model=model
        )

    for name in settings.llm.video_chain:
        if time.monotonic() >= deadline:
            return none_answered(LLMUnavailableError(TOO_LONG), TOO_LONG)
        kind, _, named = name.partition(":")
        try:
            cls = llm_class(kind.strip().lower())
        except LLMConfigError as exc:
            said.append(_said(name, exc))
            logger.exception("AI check: %s not set up", name)
            continue
        if MediaKind.VIDEO not in cls.media:
            said.append(f"{name}: reads no video, skipped")
            logger.warning("AI check: %s reads no video, skipped", name)
            continue
        provider, model = cls.label, named.strip() or cls.default_model()
        keys: list[str | None] = [*cls.default_keys()] or [None]
        for number, key in enumerate(keys, start=1):
            if time.monotonic() >= deadline:
                return none_answered(LLMUnavailableError(TOO_LONG), TOO_LONG)
            entry = f"{name} (key {number} of {len(keys)})" if len(keys) > 1 else name
            try:
                return get_llm(LLMUse.VIDEO, provider=name, api_key=key).answered(
                    prompt, CloneAiVerdict, deadline=deadline
                )
            except LLMQuotaError as exc:
                said.append(_said(entry, exc))
                unavailable = True
                logger.warning("AI check: %s out of quota (%s)", entry, exc)
            except LLMUnavailableError as exc:
                said.append(_said(entry, exc))
                unavailable = True
                logger.warning("AI check: %s unavailable (%s)", entry, exc)
                break
            except LLMAuthError as exc:
                if number == len(keys):
                    said.append(_said(entry, exc))
                    return none_answered(exc, _reason(exc))
                # One project's key revoked: the next key still works.
                said.append(_said(entry, exc))
                logger.exception("AI check: %s rejected", entry)
            except LLMConfigError as exc:
                # A missing key, a retired or mistyped model id: every key
                # fails alike, skipped on every check until someone fixes it.
                said.append(_said(entry, exc))
                logger.exception("AI check: %s not set up", entry)
                break
            except LLMError as exc:
                said.append(_said(entry, exc))
                return none_answered(exc, _reason(exc))
    if time.monotonic() >= deadline:
        return none_answered(LLMUnavailableError(TOO_LONG), TOO_LONG)
    if not provider:
        return none_answered(LLMConfigError(NO_MODEL), NO_MODEL)
    # The kind: out of quota / down when any model was, else not set up.
    if unavailable:
        return none_answered(LLMUnavailableError(NO_ANSWER), NO_ANSWER)
    return none_answered(LLMConfigError(NO_ANSWER), NO_ANSWER)


def _said(name: str, exc: LLMError) -> str:
    """One chain entry's failure as `error_detail` keeps it: raw, whole."""
    return f"{name}: {type(exc).__name__}: {exc}" + (
        f"\n{exc.detail}" if exc.detail else ""
    )


def _kind(exc: LLMError) -> ErrorKind:
    if isinstance(exc, LLMUnavailableError):
        return "quota"
    if isinstance(exc, LLMConfigError | LLMAuthError):
        return "setup"
    return "failed"


def _reason(exc: LLMError) -> str:
    """What the card says about a model's own failure: fixed words, never
    the provider's."""
    return next((r for cls, r in _REASONS if isinstance(exc, cls)), FAILED)


def _unanswered_check(
    found: CloneAiCheck, asked: _NoAnswer, llm_s: float
) -> CloneAiCheck:
    kind = _kind(asked.error)
    detail = "\n".join(asked.said) or str(asked.error)
    # Every model out of quota or none set up: the AI check is off for every
    # reel until someone acts or the quota resets -- an error, not a warning.
    log = logger.error if kind in ("quota", "setup") else logger.warning
    log(
        "AI check of %s: no verdict (%s, %s): %s",
        found.video,
        kind,
        asked.error,
        detail,
    )
    return _failed(
        found.model_copy(
            update={"provider": asked.provider, "model": asked.model, "llm_s": llm_s}
        ),
        asked.reason,
        kind,
        detail,
    )


def _failed(
    found: CloneAiCheck, error: str, kind: ErrorKind, detail: str
) -> CloneAiCheck:
    return found.model_copy(
        update={"error": error, "error_kind": kind, "error_detail": detail}
    )


def sheet_at(video: Path, dest: Path, stills: int) -> Path:
    """The contact sheet a check with `stills` stills shows the model."""
    s = settings.clone_ai_check
    return contact_sheet(video, dest, stills, s.frame_px, s.ffmpeg_timeout_s)
