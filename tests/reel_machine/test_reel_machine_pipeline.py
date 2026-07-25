"""
pipeline.draft_script's vision step: a provider's analyze_reel() result
should override the teardown's (edit me) hook/viral_mechanic/camera_look
placeholders, and a provider that can't do vision (or fails) must never
break the job -- the placeholders just stay in place. draft_script_full
additionally surfaces main_subject -- the vision step's internal
subject-targeting aid -- without merging it into the script text itself.
"""

from unittest import mock

from ofmhelpers.reel_machine.intake import IntakeResult, Transcript, Word
from ofmhelpers.reel_machine.llm.template_provider import TemplateProvider
from ofmhelpers.reel_machine.pipeline import (
    clamp_duration,
    draft_script,
    draft_script_full,
)


def _intake_result(tmp_path, duration=15.0) -> IntakeResult:
    contact_sheet = tmp_path / "contact-sheet.jpg"
    contact_sheet.write_bytes(b"fake jpg bytes")
    transcript = Transcript(
        text="hi there", words=[Word("hi", 0.0, 0.3), Word("there", 0.3, 0.6)]
    )
    return IntakeResult(
        video_path=tmp_path / "reference.mp4",
        frames_dir=tmp_path / "frames",
        contact_sheet=contact_sheet,
        transcript=transcript,
        duration=duration,
    )


def test_template_provider_never_calls_vision_and_keeps_placeholders(tmp_path):
    assert TemplateProvider().analyze_reel(tmp_path / "x.jpg", "text") == {}

    script = draft_script(_intake_result(tmp_path), llm_provider="template")
    assert "(edit me)" in script  # viral_mechanic/camera_look untouched


def test_vision_analysis_overrides_the_placeholders(tmp_path):
    fake_provider = mock.Mock()
    fake_provider.name = "fake-vision"
    fake_provider.analyze_reel.return_value = {
        "main_subject": "a young woman in a black hoodie, center-frame",
        "hook": "she looks straight into the lens and says nothing for a beat",
        "viral_mechanic": "call-out hook -> withhold -> deadpan twist",
        "camera_look": "GoPro porthole, held low, strong barrel distortion",
    }
    fake_provider.write_prompt_package.side_effect = (
        lambda teardown, shape, look, duration, target="", gender="female": teardown.camera_look
    )

    with mock.patch(
        "ofmhelpers.reel_machine.pipeline.get_provider", return_value=fake_provider
    ):
        script = draft_script(_intake_result(tmp_path))
        result = draft_script_full(_intake_result(tmp_path))

    assert script == "GoPro porthole, held low, strong barrel distortion"
    assert result.script == "GoPro porthole, held low, strong barrel distortion"
    assert result.main_subject == "a young woman in a black hoodie, center-frame"

    # main_subject is passed through as a targeting aid only -- never baked
    # into the generated script text.
    assert "black hoodie" not in result.script


def test_analyze_reel_is_called_with_the_video_path_for_full_temporal_context(tmp_path):
    fake_provider = mock.Mock()
    fake_provider.name = "fake-vision"
    fake_provider.analyze_reel.return_value = {}
    fake_provider.write_prompt_package.side_effect = (
        lambda teardown, shape, look, duration, target="", gender="female": "ok"
    )
    intake = _intake_result(tmp_path)

    with mock.patch(
        "ofmhelpers.reel_machine.pipeline.get_provider", return_value=fake_provider
    ):
        draft_script(intake)

    assert (
        fake_provider.analyze_reel.call_args.kwargs["video_path"] == intake.video_path
    )


def test_failed_vision_analysis_falls_back_to_placeholders_without_raising(tmp_path):
    fake_provider = mock.Mock()
    fake_provider.name = "flaky-vision"
    fake_provider.analyze_reel.side_effect = RuntimeError("vision call failed")
    fake_provider.write_prompt_package.side_effect = (
        lambda teardown, shape, look, duration, target="", gender="female": teardown.camera_look
    )

    with mock.patch(
        "ofmhelpers.reel_machine.pipeline.get_provider", return_value=fake_provider
    ):
        script = draft_script(_intake_result(tmp_path))

    assert "(edit me)" in script


def test_duration_defaults_to_the_source_reels_own_duration(tmp_path):
    fake_provider = mock.Mock()
    fake_provider.name = "fake"
    fake_provider.analyze_reel.return_value = {}
    seen_durations = []
    fake_provider.write_prompt_package.side_effect = (
        lambda teardown, shape, look, duration, target="", gender="female": (
            seen_durations.append(duration) or "ok"
        )
    )

    with mock.patch(
        "ofmhelpers.reel_machine.pipeline.get_provider", return_value=fake_provider
    ):
        draft_script(_intake_result(tmp_path, duration=8.0))

    assert seen_durations == [8]


def test_clamp_duration_stays_within_seedances_supported_range():
    assert clamp_duration(2.0) == 4
    assert clamp_duration(30.0) == 15
    assert clamp_duration(9.4) == 9
