"""
Validation for the model's answer to `prompts.ANALYSIS_PROMPT`.

The provider is asked for JSON, but "asked for JSON" is not "got JSON": a
model can wrap it in a ``` fence, prepend "Here is the prompt:", or drop
half the keys. `parse_analysis` is the single gate between a provider's raw
text and anything the rest of the app (job result, review page, the actual
Seedance call) treats as a prompt -- a failure here fails the job with a
readable message instead of handing a VA a broken prompt to fire.
"""

from __future__ import annotations

import json

# Every top-level key ANALYSIS_PROMPT asks for. A response missing any of
# them is a bad answer, not a partial one -- Seedance is fed the whole
# object, so a dropped "scene_events" silently produces a very different
# video rather than a slightly worse one.
REQUIRED_KEYS = (
    "format",
    "people",
    "environment",
    "lighting",
    "color_grading",
    "atmosphere",
    "audio",
    "pacing",
    "background_activity",
    "scene_events",
    "style",
    "camera_logic",
    "imperfections",
    "shots",
    "end_behavior",
    "negative_prompt",
)

_LIST_KEYS = ("people", "scene_events", "imperfections", "shots")


class AnalysisError(ValueError):
    """The provider's response wasn't a usable Seedance prompt."""


def strip_code_fence(text: str) -> str:
    """Drop a ``` / ```json fence a model wrapped the JSON in, plus any
    prose before the first `{`. The prompt says "no markdown, no backticks",
    but models add them anyway and it's a one-line fix here versus a failed
    job."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        return cleaned.strip()
    return cleaned[start : end + 1]


def parse_analysis(text: str) -> dict:
    """Raw provider text -> the validated prompt dict. Raises AnalysisError
    (surfaced as the job's error message) if it isn't JSON, isn't an object,
    or is missing/mistyped any key ANALYSIS_PROMPT asked for."""
    try:
        data = json.loads(strip_code_fence(text))
    except json.JSONDecodeError as exc:
        msg = f"model did not return valid JSON: {exc}"
        raise AnalysisError(msg) from exc

    if not isinstance(data, dict):
        msg = f"expected a JSON object, got {type(data).__name__}"
        raise AnalysisError(msg)

    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        msg = f"model response is missing required keys: {', '.join(missing)}"
        raise AnalysisError(msg)

    wrong_type = [key for key in _LIST_KEYS if not isinstance(data[key], list)]
    if wrong_type:
        msg = f"expected a list for: {', '.join(wrong_type)}"
        raise AnalysisError(msg)

    return data
