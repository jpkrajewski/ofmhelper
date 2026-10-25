"""Is a generated reel too glitchy to post?

The whole video becomes one contact sheet (`llm.frames`: a still every
`settings.clone_ai_check.every_s`, first and last frame included, each labelled
with its time), which goes with `RULES` to the video model
(`LLM_DEFAULT_VIDEO`). It answers a `CloneAiVerdict`: its reasoning, every
glitch with its time and severity, how sure it is (0-100) and GOOD or
GLITCHY.

Never raises for what can go wrong with a video or a model: an unreadable
video or an `LLMError` comes back as a `CloneAiCheck` with `error` set and no
verdict, so the caller keeps the failure like any result. The sheet lives
in a temporary folder for the call only; the stored still times are enough
to draw it again (`sheet`).
"""

import math
import subprocess
import tempfile
import time
from pathlib import Path

from ofmhelpers.clone_ai_check.models import CloneAiCheck, CloneAiVerdict
from ofmhelpers.config import settings
from ofmhelpers.llm import (
    LLMConfigError,
    LLMError,
    LLMUnavailableError,
    LLMUse,
    Prompt,
    get_llm,
)
from ofmhelpers.llm.base import Answered
from ofmhelpers.llm.frames import contact_sheet, still_times
from ofmhelpers.log import get_logger
from ofmhelpers.utils.video_probe import duration_s

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
Keep it short: a few words per problem, one sentence of reasoning."""

_ERRORS = (OSError, RuntimeError, ValueError, subprocess.SubprocessError)


def still_count(seconds: float) -> int:
    """Stills for a video `seconds` long: one per `every_s`, both ends
    included, between 2 and `max_frames`."""
    s = settings.clone_ai_check
    return max(2, min(s.max_frames, math.floor(seconds / s.every_s) + 1))


def prompt_text(video: Path, seconds: float, stills: int) -> str:
    return (
        f"Contact sheet of {video.name} ({seconds:.1f} s, {stills} stills)."
        " Is it too glitchy to post?"
    )


def check_clone(video: Path) -> CloneAiCheck:
    """The reel at `video` checked. See the module docstring."""
    s = settings.clone_ai_check
    started = time.monotonic()
    found = CloneAiCheck(
        video=str(video), frame_px=s.frame_px, prompt_system=RULES, prompt_text=""
    )
    try:
        seconds = duration_s(video, s.ffmpeg_timeout_s)
    except _ERRORS as exc:
        logger.warning("AI check: cannot read %s", video, exc_info=True)
        return found.model_copy(update={"error": f"Could not read the video: {exc}"})
    count = still_count(seconds)
    text = prompt_text(video, seconds, count)
    found = found.model_copy(update={"video_seconds": seconds, "prompt_text": text})
    with tempfile.TemporaryDirectory(prefix="clone_ai_check-") as work:
        try:
            sheet = sheet_at(video, Path(work) / "sheet.jpg", count)
        except _ERRORS as exc:
            logger.warning("AI check: no contact sheet of %s", video, exc_info=True)
            return found.model_copy(
                update={"error": f"Could not make the contact sheet: {exc}"}
            )
        found = found.model_copy(
            update={
                "stills_at": still_times(seconds, count),
                "sheet_s": round(time.monotonic() - started, 3),
            }
        )
        try:
            answered = _ask(Prompt.of(text, sheet, system=RULES))
        except LLMError as exc:
            logger.warning("AI check of %s failed: %s", video, exc)
            error = f"{exc} | {exc.detail}" if exc.detail else str(exc)
            return found.model_copy(update={"error": error})
    verdict = answered.answer
    return found.model_copy(
        update={
            "verdict": verdict.verdict,
            "glitch_percent": verdict.glitch_percent,
            "glitches": verdict.glitches,
            "reasoning": verdict.reasoning,
            "provider": answered.provider,
            "model": answered.model,
            "raw_answer": answered.text,
            "attempts": answered.attempts,
            "llm_s": answered.seconds,
        }
    )


def _ask(prompt: Prompt) -> Answered[CloneAiVerdict]:
    """The first answer along `settings.llm.video_chain` (Gemini, then
    Groq...): a provider down, rate limited, out of quota or not set up hands
    over to the next; any other failure (refused, a wrong answer) is the
    answer. Raises the last provider's error when none answered."""
    chain = settings.llm.video_chain
    for i, name in enumerate(chain):
        try:
            return get_llm(LLMUse.VIDEO, provider=name).answered(prompt, CloneAiVerdict)
        except (LLMUnavailableError, LLMConfigError) as exc:
            if i == len(chain) - 1:
                raise
            logger.warning(
                "AI check: %s unavailable (%s), asking %s", name, exc, chain[i + 1]
            )
    msg = "No video model configured (LLM_DEFAULT_VIDEO)"
    raise LLMConfigError(msg)


def sheet_at(video: Path, dest: Path, stills: int) -> Path:
    """The contact sheet a check with `stills` stills shows the model."""
    s = settings.clone_ai_check
    return contact_sheet(video, dest, stills, s.frame_px, s.ffmpeg_timeout_s)
