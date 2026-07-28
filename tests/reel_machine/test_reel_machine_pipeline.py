"""
pipeline.analyze is three steps with no branches (intake -> provider ->
validate); what's worth testing is that the fixed prompt is what gets sent,
that a bad response fails the job instead of degrading it, and that the
duration is clamped to Seedance's range.
"""

import json
from unittest import mock

import pytest

from ofmhelpers.reel_machine import pipeline
from ofmhelpers.reel_machine.intake import IntakeResult
from ofmhelpers.reel_machine.prompts import ANALYSIS_PROMPT
from ofmhelpers.reel_machine.schema import REQUIRED_KEYS, AnalysisError


def _payload(**overrides) -> str:
    data = {
        key: [] if key in ("people", "scene_events", "imperfections", "shots") else "x"
        for key in REQUIRED_KEYS
    }
    data.update(overrides)
    return json.dumps(data)


class FakeProvider:
    name = "fake"

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple] = []

    def analyze_video(self, video_path, prompt):
        self.calls.append((video_path, prompt))
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


def test_sends_the_video_and_the_fixed_prompt(video):
    provider, result = _run(video, _payload(environment="a lift lobby"))

    assert provider.calls == [(video, ANALYSIS_PROMPT)]
    assert result.prompt["environment"] == "a lift lobby"
    assert result.provider == "fake"
    assert result.video_path == video


def test_prompt_text_is_the_validated_object_pretty_printed(video):
    _, result = _run(video, _payload(environment="a lift lobby"))
    assert json.loads(result.prompt_text)["environment"] == "a lift lobby"
    assert "\n" in result.prompt_text


def test_duration_comes_from_the_source_reel_and_is_clamped(video):
    assert _run(video, _payload(), duration=12.4)[1].duration == 12
    assert _run(video, _payload(), duration=41.0)[1].duration == pipeline.MAX_DURATION_S
    assert _run(video, _payload(), duration=1.2)[1].duration == pipeline.MIN_DURATION_S


def test_a_bad_response_fails_the_job(video):
    """No fallback provider, no partial prompt: a prompt the model didn't
    actually produce is worse than a failed job."""
    with pytest.raises(AnalysisError):
        _run(video, "sorry, I can't help with that")
