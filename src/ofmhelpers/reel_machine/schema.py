"""
The typed shape of the model's answer to `prompts.ANALYSIS_PROMPT`.

The provider is asked for JSON, but "asked for JSON" is not "got JSON": a
model can wrap it in a ``` fence, prepend "Here is the prompt:", or drop
half the keys. `parse_analysis` is the single gate between a provider's raw
text and a `ReelAnalysis` the rest of the app treats as a prompt.

Validation is strict (no int-where-a-string-was-asked-for coercion), but it
is **not** the end of the job: a response that fails validation is still
handed to the user as raw text (see `AnalysisError.raw` and
`pipeline.analyze`), because a VA reading a slightly-wrong prompt is more
useful than a dead job with nothing to show.

Unknown keys are rejected (`extra="forbid"`): the prompt spells out the exact
object it wants, so a key nobody asked for means the model went off-script,
and the raw fallback shows the whole answer rather than a shape the rest of
the app half-understands.
"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError


class _Section(BaseModel):
    # strict: "3" is not 3 and 3 is not "3" -- a model that answers with the
    # wrong type answered wrong, and the raw fallback shows it verbatim.
    model_config = ConfigDict(extra="forbid", strict=True)


class Person(_Section):
    id: str
    role: str
    appearance: str
    wardrobe: str


class SceneEvent(_Section):
    timestamp: str
    speaker: str
    line: str | None = None
    delivery: str | None = None
    action: str
    pose: str | None = None
    facial_expression: str | None = None


class Shot(_Section):
    time: str
    action: str
    camera_behavior: str
    scene_event_cue: (
        Annotated[str, StringConstraints(pattern=r"^\d+:\d{2}$")] | None
    ) = None


class ReelAnalysis(_Section):
    viral_factor: str
    context: str
    format: str
    people: list[Person]
    environment: str
    lighting: str
    color_grading: str
    atmosphere: str
    audio: str
    pacing: str
    background_activity: str
    scene_events: list[SceneEvent]
    style: str
    camera_logic: str
    imperfections: list[str]
    shots: list[Shot]
    end_behavior: str
    negative_prompt: str

    @property
    def subject(self) -> Person | None:
        """The person the clone is *of*. Everyone else in `people` is a
        cameraman, a passer-by or a friend off camera -- their wardrobe and
        role are noise for anything that describes the model herself (the
        hunts, the wardrobe searches). The prompt asks for the main subject
        under the id "subject"; a model that named it something else still
        puts her first."""
        for person in self.people:
            if person.id == "subject":
                return person
        return self.people[0] if self.people else None

    def elevenlabs_ready_prompt_from_subject(self, speaker: str = "subject") -> str:
        """Just what one person says, in ElevenLabs' bracket-cue form:

            [delivery] line [pause] [delivery] next line

        Delivery becomes the bracketed emotion cue ElevenLabs reads as
        direction; `[pause]` marks each gap between spoken moments, which is
        where the silent scene_events entries sat. Events with no `line` are
        the silent ones -- they contribute the pause, not text.
        """
        segments = [
            f"[{event.delivery}] {event.line}" if event.delivery else event.line
            for event in self.scene_events
            if event.speaker == speaker and event.line
        ]
        return " [pause] ".join(segments)

    @property
    def prompt_text(self) -> str:
        """What Seedance is given and what the review textarea shows."""
        return json.dumps(self.model_dump(), indent=2, ensure_ascii=False)


# Kept as the prompt/schema drift check (tests assert ANALYSIS_PROMPT still
# asks for every one of these); the real validation is ReelAnalysis itself.
REQUIRED_KEYS = tuple(ReelAnalysis.model_fields)


class AnalysisError(ValueError):
    """The provider's response wasn't a usable Seedance prompt. Carries the
    provider's raw text so the caller can show it instead of nothing."""

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


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


def parse_analysis(text: str) -> ReelAnalysis:
    """Raw provider text -> a validated ReelAnalysis. Raises AnalysisError
    (with `.raw` set to `text`) if it isn't JSON, isn't an object, or doesn't
    match the shape ANALYSIS_PROMPT asked for."""
    try:
        data = json.loads(strip_code_fence(text))
    except json.JSONDecodeError as exc:
        msg = f"model did not return valid JSON: {exc}"
        raise AnalysisError(msg, raw=text) from exc

    if not isinstance(data, dict):
        msg = f"expected a JSON object, got {type(data).__name__}"
        raise AnalysisError(msg, raw=text)

    try:
        return ReelAnalysis.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
            for err in exc.errors()
        )
        msg = f"model response doesn't match the prompt's shape: {problems}"
        raise AnalysisError(msg, raw=text) from exc
