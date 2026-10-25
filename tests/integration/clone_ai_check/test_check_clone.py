"""`check_clone`: a whole video as one contact sheet to the video model,
answered GOOD or GLITCHY with its reasoning -- and everything that can go
wrong with the video or the model kept as the check's `error`, never
raised."""

from pathlib import Path

import pytest
from PIL import Image

from ofmhelpers.clone_ai_check import RULES, Verdict, check_clone, sheet_at, still_count
from ofmhelpers.llm.gemini.fake import FAKE_KEY, fake_gemini
from ofmhelpers.utils import fake_media

GLITCHY = {
    "reasoning": "The left hand grows a sixth finger at 0:01.0.",
    "glitches": [{"at_s": 1.0, "what": "six fingers", "severity": "blocking"}],
    "glitch_percent": 88,
    "verdict": "GLITCHY",
}


@pytest.fixture
def video(fake_externals, monkeypatch, tmp_path) -> Path:
    """A 2 s reel, Gemini on its fake, no retries."""
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OFM_LLM_RETRIES", "0")
    path = tmp_path / "reel.mp4"
    path.write_bytes(fake_media.mp4())
    return path


@pytest.mark.parametrize(
    ("seconds", "stills"), [(0.4, 2), (2.0, 3), (15.0, 16), (40.0, 16)]
)
def test_one_still_a_second_both_ends_included_at_most_16(seconds, stills):
    assert still_count(seconds) == stills


def test_a_clean_reel_is_good_with_everything_kept_to_debug_it(video):
    found = check_clone(video)

    assert found.error is None
    assert found.verdict is Verdict.GOOD
    assert found.glitch_percent == 0
    assert found.provider == "Gemini"
    assert found.model == "gemini-3.8-flash"
    assert found.prompt_system == RULES
    assert "Contact sheet of reel.mp4 (2.0 s, 3 stills)" in found.prompt_text
    assert found.raw_answer.startswith("{")
    assert found.stills_at == [0.0, 0.95, 1.9]
    assert found.attempts == 1
    assert found.video_seconds == pytest.approx(2.0, abs=0.1)
    [request] = fake_gemini().requests
    parts = request["contents"][0]["parts"]
    assert parts[0]["text"] == found.prompt_text
    assert parts[1]["inlineData"]["mime_type"] == "image/jpeg"  # the sheet


def test_a_glitchy_answer_carries_its_glitches(video):
    fake_gemini().answer_next(GLITCHY)

    found = check_clone(video)

    assert found.glitchy
    assert found.glitch_percent == 88
    assert [g.what for g in found.glitches] == ["six fingers"]
    assert found.reasoning.startswith("The left hand")


def test_a_model_failure_is_the_checks_error_not_an_exception(video):
    fake_gemini().fail_next(429, "RESOURCE_EXHAUSTED", "Quota exceeded")

    found = check_clone(video)

    assert found.verdict is None
    assert "Gemini" in found.error
    assert "Quota exceeded" in found.error
    assert found.stills_at  # the sheet was made: the model failed


def test_an_unreadable_video_is_the_checks_error(fake_externals, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")

    found = check_clone(broken)

    assert found.verdict is None
    assert found.error.startswith("Could not read the video")
    assert fake_gemini().requests == []


def test_the_sheet_tiles_every_still_in_time_order(video, tmp_path):
    sheet = sheet_at(video, tmp_path / "sheet.jpg", 4)

    with Image.open(sheet) as image:
        width, height = image.size
    assert width == 2 * 360  # 2 x 2 stills, each 360 px wide
    assert height > width  # a 9:16 reel's stills
