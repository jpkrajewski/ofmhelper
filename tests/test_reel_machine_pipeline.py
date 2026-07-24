"""
pipeline.draft_script's vision step: a provider's analyze_reel() result
should override the teardown's (edit me) hook/viral_mechanic/camera_look
placeholders, and a provider that can't do vision (or fails) must never
break the job -- the placeholders just stay in place.
"""

from unittest import mock

from ofmhelpers.reel_machine.intake import IntakeResult, Transcript, Word
from ofmhelpers.reel_machine.llm.template_provider import TemplateProvider
from ofmhelpers.reel_machine.pipeline import draft_script


def _intake_result(tmp_path) -> IntakeResult:
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
    )


def test_template_provider_never_calls_vision_and_keeps_placeholders(tmp_path):
    assert TemplateProvider().analyze_reel(tmp_path / "x.jpg", "text") == {}

    script = draft_script(_intake_result(tmp_path), llm_provider="template")
    assert "(edit me)" in script  # viral_mechanic/camera_look untouched


def test_vision_analysis_overrides_the_placeholders(tmp_path):
    fake_provider = mock.Mock()
    fake_provider.name = "fake-vision"
    fake_provider.analyze_reel.return_value = {
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

    assert script == "GoPro porthole, held low, strong barrel distortion"


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
