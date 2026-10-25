"""GOOD or GLITCHY read off a model's answer, the prompt a mirrored reel
gets, and the check's deadline against the review's RQ limit."""

from pathlib import Path

import pytest

from ofmhelpers.clone_ai_check import CloneAiVerdict, Verdict
from ofmhelpers.clone_ai_check.check import prompt_text
from ofmhelpers.config import settings


def _answer(severities: list[str], percent: int) -> CloneAiVerdict:
    return CloneAiVerdict.model_validate(
        {
            "reasoning": "x",
            "glitches": [
                {"at_s": 1.0, "what": "hand", "severity": s} for s in severities
            ],
            "glitch_percent": percent,
        }
    )


@pytest.mark.parametrize(
    ("severities", "percent", "verdict"),
    [
        ([], 0, Verdict.GOOD),
        (["minor"], 49, Verdict.GOOD),
        (["minor"], 50, Verdict.GLITCHY),  # minor ones add up: unsure = TRIAL
        (["minor"], 90, Verdict.GLITCHY),
        ([], 90, Verdict.GLITCHY),
        (["blocking"], 0, Verdict.GLITCHY),  # one blocking glitch is enough
        (["minor", "blocking"], 10, Verdict.GLITCHY),
    ],
)
def test_glitchy_when_a_glitch_blocks_or_the_model_is_unsure(
    severities, percent, verdict
):
    assert _answer(severities, percent).verdict(50) is verdict


def test_the_verdict_is_not_asked_of_the_model():
    assert "verdict" not in CloneAiVerdict.model_json_schema()["properties"]


def test_a_mirrored_reel_is_told_its_backwards_text_is_no_glitch():
    plain = prompt_text(Path("reel.mp4"), 2.0, 3)
    mirrored = prompt_text(Path("reel.mp4"), 2.0, 3, mirrored=True)

    assert "mirrored" not in plain
    assert mirrored.startswith("Contact sheet of reel.mp4 (2.0 s, 3 stills).")
    assert "mirrored left to right on purpose" in mirrored
    assert "backwards are not a glitch" in mirrored
    assert mirrored.endswith("Is it too glitchy to post?")


def test_the_check_ends_well_inside_the_reviews_time_limit():
    """The deadline, one request still in flight past it, and the upload
    all fit in RQ's limit for one review."""
    check = settings.clone_ai_check
    assert check.deadline_s == 600
    assert check.glitchy_percent == 50
    in_flight = settings.llm.timeout_s
    assert check.deadline_s + in_flight < settings.clone_links.review_timeout_s
