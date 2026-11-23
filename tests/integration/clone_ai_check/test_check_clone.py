# Copyright (C) 2026 jpkrajewski

import logging
import time
lazy from pathlib import Path

import pytest
from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.features.clone_links.ai_check import (
    Verdict,
    check_clone,
    sheet_at,
    still_count,
)
from ofmhelpers.features.clone_links.ai_check import check as check_module
from ofmhelpers.features.clone_links.constants import clone_links_constants
from ofmhelpers.features.clone_links.folders import folder_for
from ofmhelpers.integrations.llm.base import Answered
from ofmhelpers.integrations.llm.gemini.fake import FAKE_KEY, FAKE_KEY_2, fake_gemini
from ofmhelpers.integrations.llm.groq.fake import FAKE_KEY as GROQ_KEY
from ofmhelpers.integrations.llm.groq.fake import fake_groq
from ofmhelpers.media import fake_media

GLITCHY = {
    "reasoning": "The left hand grows a sixth finger at 0:01.0.",
    "glitches": [{"at_s": 1.0, "what": "six fingers", "severity": "blocking"}],
    "glitch_percent": 88,
}
QUOTA = (429, "RESOURCE_EXHAUSTED", "Quota exceeded")


@pytest.fixture
def video(fake_externals, monkeypatch, tmp_path) -> Path:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("OFM_LLM_RETRIES", "0")
    path = tmp_path / "reel.mp4"
    path.write_bytes(fake_media.mp4())
    return path


class _Clock:
    def __init__(self) -> None:
        self.ahead = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self.ahead


@pytest.mark.parametrize(
    ("seconds", "stills"),
    [(0.4, 2), (2.0, 3), (15.0, 16), (40.0, 16)],
)
def test_one_still_a_second_both_ends_included_at_most_16(seconds, stills):
    assert still_count(seconds) == stills


def test_a_clean_reel_is_good_with_everything_kept_to_debug_it(video):
    found = check_clone(video)

    assert found.error is None
    assert found.error_kind is None
    assert found.verdict is Verdict.GOOD
    assert found.glitch_percent == 0
    assert found.provider == "Gemini"
    assert found.model == "gemini-3.6-flash"
    assert found.prompt_system == clone_links_constants().rules
    assert "Contact sheet of reel.mp4 (2.0 s, 3 stills)" in found.prompt_text
    assert "mirrored" not in found.prompt_text
    assert found.raw_answer.startswith("{")
    assert found.stills_at == [0.0, 0.95, 1.9]
    assert found.attempts == 1
    assert found.video_seconds == pytest.approx(2.0, abs=0.1)
    [request] = fake_gemini().requests
    parts = request["contents"][0]["parts"]
    assert parts[0]["text"] == found.prompt_text
    assert parts[1]["inlineData"]["mime_type"] == "image/jpeg"


def test_a_glitchy_answer_carries_its_glitches(video):
    fake_gemini().answer_next(GLITCHY)

    found = check_clone(video)

    assert found.glitchy
    assert found.glitch_percent == 88
    assert [g.what for g in found.glitches] == ["six fingers"]
    assert found.reasoning.startswith("The left hand")


def test_minor_glitches_the_model_is_sure_of_go_to_trial_reels(video):
    fake_gemini().answer_next(
        {
            "reasoning": "Small hand and shirt problems add up.",
            "glitches": [{"at_s": 1.0, "what": "hand", "severity": "minor"}],
            "glitch_percent": 90,
        },
    )

    found = check_clone(video)

    assert found.verdict is Verdict.GLITCHY
    assert folder_for(found) == settings.clone_links.trial_folder


def test_no_glitch_and_zero_percent_is_good_reels(video):
    fake_gemini().answer_next(
        {"reasoning": "Clean.", "glitches": [], "glitch_percent": 0},
    )

    found = check_clone(video)

    assert found.verdict is Verdict.GOOD
    assert folder_for(found) == settings.clone_links.good_folder


def test_a_mirrored_reel_tells_the_model_its_text_reads_backwards(video):
    found = check_clone(video, mirrored=True)

    assert "mirrored left to right on purpose" in found.prompt_text
    [request] = fake_gemini().requests
    assert request["contents"][0]["parts"][0]["text"] == found.prompt_text


def test_the_tokens_the_answer_used_are_kept(video, monkeypatch):
    deadlines = []

    class Counted:
        def answered(self, _prompt, answer, *, deadline=None):
            deadlines.append(deadline)
            return Answered(
                answer=answer.model_validate(GLITCHY),
                text="{}",
                provider="Gemini",
                model="gemini-3.6-flash",
                attempts=1,
                seconds=1.5,
                input_tokens=1200,
                thinking_tokens=300,
                output_tokens=80,
            )

    monkeypatch.setattr(check_module, "get_llm", lambda *a, **k: Counted())
    whole = settings.clone_ai_check.deadline_s
    asked = time.monotonic()

    found = check_clone(video)

    assert (found.input_tokens, found.thinking_tokens, found.output_tokens) == (
        1200,
        300,
        80,
    )
    assert found.llm_s == 1.5
    [deadline] = deadlines
    assert asked + whole <= deadline <= time.monotonic() + whole


def test_a_model_failure_is_the_checks_error_not_an_exception(video, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_VIDEO", "")
    fake_gemini().fail_next(*QUOTA)

    found = check_clone(video)

    assert found.verdict is None
    assert found.error == clone_links_constants().no_answer
    assert found.error_kind == "quota"
    assert "Quota exceeded" in found.error_detail
    assert (found.provider, found.model) == ("Gemini", "gemini-3.6-flash")
    assert found.stills_at
    assert folder_for(found) == settings.clone_links.trial_folder


def test_a_model_out_of_quota_hands_the_check_to_the_next_gemini(video):
    fake_gemini().fail_next(*QUOTA)

    found = check_clone(video)

    assert found.error is None
    assert (found.provider, found.model) == ("Gemini", "gemini-3.8-flash")


def test_a_key_out_of_quota_hands_the_same_model_to_the_next_key(video, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", f"{FAKE_KEY},{FAKE_KEY_2}")
    fake_gemini().fail_next(*QUOTA)

    found = check_clone(video)

    assert found.error is None
    assert found.model == "gemini-3.6-flash"
    assert fake_gemini().keys == [FAKE_KEY, FAKE_KEY_2]


def test_an_overloaded_model_goes_to_the_next_model_not_the_next_key(
    video,
    monkeypatch,
):
    monkeypatch.setenv("GEMINI_API_KEY", f"{FAKE_KEY},{FAKE_KEY_2}")
    fake_gemini().fail_next(503, "UNAVAILABLE", "This model is overloaded")

    found = check_clone(video)

    assert found.error is None
    assert fake_gemini().models == ["gemini-3.6-flash", "gemini-3.8-flash"]
    assert fake_gemini().keys == [FAKE_KEY, FAKE_KEY]


def test_a_rejected_key_hands_over_to_the_next_key(video, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", f"revoked-key, {FAKE_KEY}")

    found = check_clone(video)

    assert found.error is None
    assert found.model == "gemini-3.6-flash"
    assert fake_gemini().keys == [FAKE_KEY], "the revoked key got no answer"


def test_every_gemini_out_of_quota_is_an_error_and_groq_never_judges(
    video,
    monkeypatch,
    caplog,
):
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    for _ in range(4):
        fake_gemini().fail_next(*QUOTA)

    with caplog.at_level(logging.WARNING, logger=check_module.__name__):
        found = check_clone(video)

    assert found.verdict is None
    assert found.error == clone_links_constants().no_answer
    assert found.error_kind == "quota"
    chain = settings.llm.video_chain
    assert len(chain) == 4
    for name in chain:
        assert f"{name}: LLMQuotaError" in found.error_detail
    assert found.error_detail.count("Quota exceeded") == 4
    assert (found.provider, found.model) == ("Gemini", "gemini-3.5-flash-lite")
    assert fake_gemini().models == [
        "gemini-3.6-flash",
        "gemini-3.8-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
    ]
    assert fake_groq().requests == []
    [ended] = [r for r in caplog.records if "no verdict" in r.getMessage()]
    assert ended.levelno == logging.ERROR


def test_an_image_only_provider_in_the_chain_is_skipped(video, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_VIDEO", "groq")
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    fake_gemini().fail_next(*QUOTA)

    found = check_clone(video)

    assert found.verdict is None
    assert found.error == clone_links_constants().no_answer
    assert found.error_kind == "quota"
    assert "groq: reads no video, skipped" in found.error_detail
    assert fake_groq().requests == []
    assert folder_for(found) == settings.clone_links.trial_folder


def test_a_chain_of_image_only_providers_is_a_setup_error(video, monkeypatch):
    monkeypatch.setenv("LLM_DEFAULT_VIDEO", "groq")
    monkeypatch.setenv("LLM_FALLBACK_VIDEO", "cerebras")
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)

    found = check_clone(video)

    assert found.verdict is None
    assert (found.error, found.error_kind) == (
        clone_links_constants().no_model,
        "setup",
    )
    assert fake_groq().requests == []
    assert fake_gemini().requests == []


def test_no_key_is_a_setup_error_logged_as_one(video, monkeypatch, caplog):
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("LLM_FALLBACK_VIDEO", "gemini:gemini-3.8-flash")

    with caplog.at_level(logging.WARNING, logger=check_module.__name__):
        found = check_clone(video)

    assert found.verdict is None
    assert (found.error, found.error_kind) == (
        clone_links_constants().no_answer,
        "setup",
    )
    assert found.error_detail.count("no API key configured") == 2
    assert fake_gemini().requests == []
    hops = [r for r in caplog.records if "not set up" in r.getMessage()]
    assert [r.levelno for r in hops] == [logging.ERROR, logging.ERROR]


def test_an_unknown_model_id_hands_over_logged_as_an_error(video, monkeypatch, caplog):
    monkeypatch.setenv("LLM_DEFAULT_VIDEO", "gemini:gemini-0-retired")

    with caplog.at_level(logging.WARNING, logger=check_module.__name__):
        found = check_clone(video)

    assert found.error is None
    assert found.model == "gemini-3.8-flash", "the next in the chain answered"
    [hop] = [r for r in caplog.records if "not set up" in r.getMessage()]
    assert hop.levelno == logging.ERROR


def test_a_wrong_shape_answer_is_a_short_error_with_the_text_kept(video, monkeypatch):
    monkeypatch.setenv("OFM_LLM_SHAPE_RETRIES", "0")
    fake_gemini().answer_next({"reasoning": "x", "glitches": "none"})

    found = check_clone(video)

    assert found.verdict is None
    assert (found.error, found.error_kind) == ("The AI gave a broken answer", "failed")
    assert "LLMOutputError" in found.error_detail
    assert '"glitches": "none"' in found.error_detail
    assert fake_gemini().models == ["gemini-3.6-flash"]


def test_no_model_is_asked_once_the_deadline_passed(video, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(check_module, "time", clock)
    fake = fake_gemini()
    answer = fake._generate

    def slow(model, body):
        clock.ahead += settings.clone_ai_check.deadline_s + 1
        return answer(model, body)

    monkeypatch.setattr(fake, "_generate", slow)
    fake.fail_next(503, "UNAVAILABLE", "The model is overloaded")

    found = check_clone(video)

    assert fake.models == ["gemini-3.6-flash"]
    assert found.verdict is None
    assert (found.error, found.error_kind) == (
        clone_links_constants().too_long,
        "quota",
    )
    assert "The model is overloaded" in found.error_detail
    assert found.llm_s > settings.clone_ai_check.deadline_s
    assert (found.provider, found.model) == ("Gemini", "gemini-3.6-flash")
    assert folder_for(found) == settings.clone_links.trial_folder


def test_a_deadline_gone_before_the_sheet_was_ready_asks_nobody(video, monkeypatch):
    monkeypatch.setenv("OFM_CLONE_AI_CHECK_DEADLINE_S", "0.001")

    found = check_clone(video)

    assert (found.error, found.error_kind) == (
        clone_links_constants().too_long,
        "quota",
    )
    assert fake_gemini().requests == []


def test_an_unreadable_video_is_the_checks_error(fake_externals, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")

    found = check_clone(broken)

    assert found.verdict is None
    assert (found.error, found.error_kind) == ("Could not read the video", "failed")
    assert found.error_detail
    assert fake_gemini().requests == []


def test_the_sheet_tiles_every_still_in_time_order(video, tmp_path):
    sheet = sheet_at(video, tmp_path / "sheet.jpg", 4)

    with Image.open(sheet) as image:
        width, height = image.size
    assert width == 2 * 360
    assert height > width
