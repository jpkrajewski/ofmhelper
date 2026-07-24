"""
The "teardown" -- the rebuild blueprint a Seedance prompt package is written
from: the hook, a per-beat breakdown of what's said and when, the viral
mechanic, and the camera look. In the old reel-machine skill bundle this was
hand-written by an interactive AI agent watching the reel; here it's a
template-based first draft (grouping the transcript's word-level timing into
beats by pause length) that the user edits in the browser before generating.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ofmhelpers.reel_machine.intake import Transcript

# A gap this long or longer between two words marks a beat boundary -- this
# is the same "word-level gaps are the pause structure" idea the original
# reel-intake skill used to find the pacing/beat map.
BEAT_GAP_S = 0.5


@dataclass
class Beat:
    start: float
    end: float
    text: str
    # "MAIN" is a placeholder resolved to the chosen gender's speaker tag
    # (SHE/HE/THEY) by prompt_builder._format_timeline -- set an explicit
    # value here (e.g. "MAN", "WOMAN 2") for a second voice that shouldn't
    # follow the main character's gender.
    speaker: str = "MAIN"
    on_cam: bool = True


@dataclass
class Teardown:
    hook: str
    beats: list[Beat] = field(default_factory=list)
    viral_mechanic: str = (
        "(edit me) e.g. call-out hook -> withhold -> debatable twist -> comment bait"
    )
    camera_look: str = (
        "(edit me) describe the lens/vignette/camera height/holder -- see reel_machine.looks"
    )
    duration: float = 15.0


def _group_into_beats(words: list, duration: float) -> list[Beat]:
    if not words:
        return [
            Beat(
                start=0.0,
                end=duration,
                text="(no words transcribed -- type the line here)",
            )
        ]

    beats: list[Beat] = []
    current_words = [words[0]]
    for prev, word in zip(words, words[1:]):
        if word.start - prev.end >= BEAT_GAP_S:
            beats.append(
                Beat(
                    start=current_words[0].start,
                    end=current_words[-1].end,
                    text=" ".join(w.text for w in current_words),
                )
            )
            current_words = [word]
        else:
            current_words.append(word)
    beats.append(
        Beat(
            start=current_words[0].start,
            end=current_words[-1].end,
            text=" ".join(w.text for w in current_words),
        )
    )
    return beats


def build_teardown_draft(transcript: Transcript, duration: float = 15.0) -> Teardown:
    """A deterministic first pass -- no LLM call. Groups the transcript into
    beats by pause length and takes the first beat as the hook. The result
    is meant to be edited (dialogue punched up, viral_mechanic/camera_look
    filled in) before being handed to prompt_builder.build_prompt_package."""
    beats = _group_into_beats(transcript.words, duration)
    hook = beats[0].text if beats else "(edit me) the first line that stops the scroll"
    return Teardown(hook=hook, beats=beats, duration=duration)
