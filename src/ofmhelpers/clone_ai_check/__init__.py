"""Is a generated reel too glitchy to post? `check_clone(video)`. See
clone_ai_check/CLAUDE.md."""

from ofmhelpers.clone_ai_check.check import RULES, check_clone, sheet_at, still_count
from ofmhelpers.clone_ai_check.models import (
    CloneAiCheck,
    CloneAiVerdict,
    Glitch,
    Verdict,
)

__all__ = [
    "RULES",
    "CloneAiCheck",
    "CloneAiVerdict",
    "Glitch",
    "Verdict",
    "check_clone",
    "sheet_at",
    "still_count",
]
