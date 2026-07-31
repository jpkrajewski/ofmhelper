"""
pipeline.analyze is intake -> provider -> validate; what's worth testing is
that the fixed prompt is what gets sent, that the duration is clamped to
Seedance's range, and that a response that fails validation reaches the
review page as raw text instead of killing the job.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

from ofmhelpers.reel_machine import pipeline
from ofmhelpers.reel_machine.intake import IntakeResult
from ofmhelpers.reel_machine.prompts import (
    DEFAULT_ANALYSIS_PROMPT,
    DEFAULT_ANALYSIS_SYSTEM_PROMPT,
)

EXAMPLE = Path(__file__).parent / "example.json"


def _payload(**overrides) -> str:
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data.update(overrides)
    return json.dumps(data)


class FakeProvider:
    name = "fake"

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple] = []

    def analyze_video(self, video_path, prompt, *, system_prompt=""):
        self.calls.append((video_path, prompt, system_prompt))
        return self.response


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "reference.mp4"
    path.write_bytes(b"fake video")
    return path


def _run(video, response, duration=12.4):
    provider = FakeProvider(response)
    with (
        mock.patch.object(
            pipeline,
            "run_intake",
            return_value=IntakeResult(video_path=video, duration=duration),
        ),
        mock.patch.object(pipeline, "get_provider", return_value=provider),
    ):
        return provider, pipeline.analyze("https://example.com/reel", video.parent)


def test_sends_the_video_and_the_fixed_prompt(video, monkeypatch, tmp_path):
    # Pinned at the built-in prompts: a real uploads/*.txt in the checkout
    # must not change what this test is asserting.
    monkeypatch.setenv("REEL_MACHINE_PROMPT_FILE", str(tmp_path / "none.txt"))
    monkeypatch.setenv("REEL_MACHINE_SYSTEM_PROMPT_FILE", str(tmp_path / "none.txt"))
    provider, result = _run(video, _payload(environment="a lift lobby"))

    # Both prompts come from prompts.py, not from the provider.
    assert provider.calls == [
        (video, DEFAULT_ANALYSIS_PROMPT, DEFAULT_ANALYSIS_SYSTEM_PROMPT)
    ]
    assert result.prompt.environment == "a lift lobby"
    assert result.error is None
    assert result.provider == "fake"
    assert result.video_path == video


def test_prompt_text_is_the_validated_object_pretty_printed(video):
    _, result = _run(video, _payload(environment="a lift lobby"))
    assert json.loads(result.prompt_text)["environment"] == "a lift lobby"
    assert "\n" in result.prompt_text


def test_speech_is_the_subject_s_elevenlabs_prompt(video):
    _, result = _run(video, _payload())
    assert result.speech.startswith("[playful, pleading tone] Babe")


def test_duration_comes_from_the_source_reel_and_is_clamped(video):
    assert _run(video, _payload(), duration=12.4)[1].duration == 12
    assert _run(video, _payload(), duration=41.0)[1].duration == pipeline.MAX_DURATION_S
    assert _run(video, _payload(), duration=1.2)[1].duration == pipeline.MIN_DURATION_S


def test_a_bad_response_is_handed_back_raw_instead_of_failing(video):
    """A VA who can see and fix what the model said beats a dead job."""
    _, result = _run(video, "sorry, I can't help with that")

    assert result.prompt is None
    assert "valid JSON" in result.error
    assert result.prompt_text == "sorry, I can't help with that"
    assert result.speech == ""
    # Everything not coming from the model is still usable.
    assert result.duration == 12
    assert result.provider == "fake"
