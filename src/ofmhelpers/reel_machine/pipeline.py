"""
The one entry point the web layer calls: reel in, validated Seedance prompt
JSON out.

    analyze(source, work_dir) -> AnalysisResult

Three steps, no branches: download the reel (intake), send it plus
`prompts.ANALYSIS_PROMPT` to the configured provider, validate what comes
back (schema.parse_analysis). Any failure raises and the job records the
message -- there is nothing to fall back to, since a prompt the model didn't
actually produce is worse than no prompt at all.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from ofmhelpers.log import get_logger
from ofmhelpers.reel_machine.intake import run_intake
from ofmhelpers.reel_machine.llm.registry import get_provider
from ofmhelpers.reel_machine.prompts import ANALYSIS_PROMPT
from ofmhelpers.reel_machine.schema import parse_analysis

logger = get_logger(__name__)

# Seedance 2.0's supported duration range (see kaiai/client.py's
# generate_video_seedance2) -- the source reel's own duration is clamped
# into it rather than exposed as a manual field the user has to set.
MIN_DURATION_S = 4
MAX_DURATION_S = 15


@dataclass
class AnalysisResult:
    video_path: Path
    duration: int
    prompt: dict
    provider: str

    @property
    def prompt_text(self) -> str:
        """What actually gets sent to Seedance (and what the review page
        puts in the textarea) -- the validated object, pretty-printed."""
        return json.dumps(self.prompt, indent=2, ensure_ascii=False)


def clamp_duration(seconds: float) -> int:
    return max(MIN_DURATION_S, min(MAX_DURATION_S, round(seconds)))


def analyze(
    source: str, work_dir: Path, llm_provider: str | None = None
) -> AnalysisResult:
    intake = run_intake(source, work_dir)
    provider = get_provider(llm_provider)

    logger.info(
        "analyzing %s with %s (%.1fs)",
        intake.video_path.name,
        provider.name,
        intake.duration,
    )
    raw = provider.analyze_video(intake.video_path, ANALYSIS_PROMPT)

    return AnalysisResult(
        video_path=intake.video_path,
        duration=clamp_duration(intake.duration),
        prompt=parse_analysis(raw),
        provider=provider.name,
    )
