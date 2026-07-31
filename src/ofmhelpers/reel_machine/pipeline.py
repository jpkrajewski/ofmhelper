"""
The one entry point the web layer calls: reel in, validated Seedance prompt
JSON out.

    analyze(source, work_dir) -> AnalysisResult

Three steps: download the reel (intake), send it plus
`prompts.ANALYSIS_PROMPT` to the configured provider, validate what comes
back (ReelAnalysis.from_llm_text).

Only validation is allowed to not-fail: a response that doesn't match the
schema still reaches the review page as the provider's raw text
(`AnalysisResult.prompt is None`, `error` set), because a VA who can see and
fix a slightly-wrong prompt is better off than one staring at a dead job.
Everything before that -- a failed download, a missing API key, a provider
error -- still raises, since there is nothing to show.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.reel_machine.hunt import suggest_hunt
from ofmhelpers.reel_machine.intake import run_intake
from ofmhelpers.reel_machine.llm.registry import get_provider
from ofmhelpers.reel_machine.models import AnalysisError, HuntIdeas, ReelAnalysis
from ofmhelpers.reel_machine.prompts import (
    load_analysis_prompt,
    load_analysis_system_prompt,
)

logger = get_logger(__name__)

# Seedance 2.0's supported duration range (see kaiai/client.py's
# generate_video_seedance2) -- the source reel's own duration is clamped
# into it rather than exposed as a manual field the user has to set.
MIN_DURATION_S = settings.reel_machine.min_duration_s
MAX_DURATION_S = settings.reel_machine.max_duration_s


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video_path: Path
    duration: int
    provider: str
    raw: str
    prompt: ReelAnalysis | None = None
    error: str | None = None
    # Second pass, best-effort (see hunt.py): what to search for next. Empty
    # when there is no GROQ_API_KEY, when that call failed, or when the
    # analysis itself didn't validate -- there is nothing to summarize then.
    hunt: HuntIdeas = HuntIdeas()

    @property
    def prompt_text(self) -> str:
        """What the review page puts in the textarea: the validated object
        pretty-printed, or -- when validation failed -- the provider's raw
        answer, unedited."""
        if self.prompt is None:
            return self.raw
        return json.dumps(self.prompt.model_dump(), indent=2, ensure_ascii=False)

    @property
    def speech(self) -> str:
        """The subject's lines as an ElevenLabs prompt; empty when the
        response didn't validate (there is no typed dialogue to read)."""
        if self.prompt is None:
            return ""
        return self.prompt.elevenlabs_ready_prompt_from_subject()


def clamp_duration(seconds: float) -> int:
    return max(MIN_DURATION_S, min(MAX_DURATION_S, round(seconds)))


def analyze(
    source: str,
    work_dir: Path,
    llm_provider: str | None = None,
    context: str = "",
) -> AnalysisResult:
    """`context` is the operator's note about this one reel; it rides on the
    end of the analysis prompt (see prompts.load_analysis_prompt)."""
    intake = run_intake(source, work_dir)
    provider = get_provider(llm_provider)

    logger.info(
        "analyzing %s with %s (%.1fs)",
        intake.video_path.name,
        provider.name,
        intake.duration,
    )
    raw = provider.analyze_video(
        intake.video_path,
        load_analysis_prompt(context),
        system_prompt=load_analysis_system_prompt(),
    )

    result = AnalysisResult(
        video_path=intake.video_path,
        duration=clamp_duration(intake.duration),
        provider=provider.name,
        raw=raw,
    )
    try:
        result.prompt = ReelAnalysis.from_llm_text(raw)
    except AnalysisError as exc:
        result.error = str(exc)
        logger.warning("%s returned an unusable prompt", provider.name, exc_info=True)
        return result

    # Gemini has said what the reel IS; the free text model turns that into
    # what to go LOOK FOR. Best-effort by design (see hunt.py) -- the reel is
    # already downloaded and analyzed by now.
    result.hunt = suggest_hunt(result.prompt)
    return result
