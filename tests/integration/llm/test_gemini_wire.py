"""Gemini through `ofmhelpers.llm` with Google's API faked at the wire
(llm/gemini/fake.py under the real SDK): media inline or uploaded, what is
retried and how long it waits, and every failure as the error the caller
gets -- never a raw SDK exception."""

import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from ofmhelpers.llm import (
    LLMAuthError,
    LLMConfigError,
    LLMInputError,
    LLMOutputError,
    LLMRefusedError,
    LLMUnavailableError,
    LLMUse,
    MediaKind,
    Prompt,
    get_llm,
)
from ofmhelpers.llm import base as llm_base
from ofmhelpers.llm.gemini import provider as gemini_provider
from ofmhelpers.llm.gemini.fake import FAKE_KEY, USAGE, fake_gemini, field
from ofmhelpers.llm.gemini.provider import GeminiLLM
from ofmhelpers.utils import fake_media


class Verdict(BaseModel):
    ok: bool
    problems: list[str]


class _Clock:
    """`time` as the llm modules see it: a sleep is noted and moves the
    clock on, never waited."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return time.monotonic() + sum(self.slept)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


@pytest.fixture
def clock(fake_externals, monkeypatch) -> _Clock:
    """Gemini on its fake, on a clock that never waits (only the llm
    modules': ffmpeg's own waits are real)."""
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    found = _Clock()
    monkeypatch.setattr(llm_base, "time", found)
    monkeypatch.setattr(gemini_provider, "time", found)
    return found


@pytest.fixture
def waits(clock) -> list[float]:
    """The seconds every retry and file poll would have slept."""
    return clock.slept


@pytest.fixture
def media(tmp_path) -> tuple[Path, Path]:
    image = tmp_path / "subject.jpg"
    image.write_bytes(fake_media.image())
    video = tmp_path / "clip.mp4"
    video.write_bytes(fake_media.mp4())
    return image, video


def _media(body: dict, kind: str) -> list[dict]:
    """The request's `inlineData` or `fileData` parts' bodies."""
    snake = {"inlineData": "inline_data", "fileData": "file_data"}[kind]
    return [
        found
        for c in body["contents"]
        for p in c["parts"]
        if (found := field(p, kind, snake))
    ]


def _mime(part: dict) -> str:
    return field(part, "mimeType", "mime_type")


def test_small_media_goes_inside_the_request_with_the_schema(waits, media):
    image, video = media
    fake_gemini().answer_next({"ok": False, "problems": ["hands melt"]})

    found = get_llm(LLMUse.VIDEO).ask(
        Prompt.of("Subject:", image, "Video:", video, system="You check videos."),
        Verdict,
    )

    assert found == Verdict(ok=False, problems=["hands melt"])
    [body] = fake_gemini().requests
    assert [_mime(p) for p in _media(body, "inlineData")] == ["image/jpeg", "video/mp4"]
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"]["required"] == ["ok", "problems"]
    # Deprecated for Gemini 3.x (ignored, a 400 in later generations).
    assert "temperature" not in config
    assert body["systemInstruction"]["parts"][0]["text"] == "You check videos."
    assert waits == []
    # OFM_LLM_TIMEOUT_S, less the moment reading the media took.
    assert fake_gemini().timeouts == [pytest.approx(300, abs=1)]


def test_a_temperature_the_prompt_names_is_sent(waits):
    get_llm(LLMUse.VIDEO).ask(Prompt.of("Check", temperature=0.5), Verdict)
    [body] = fake_gemini().requests
    assert body["generationConfig"]["temperature"] == 0.5


def test_the_answer_says_the_tokens_google_counted(waits):
    found = get_llm(LLMUse.VIDEO).answered(Prompt.of("Check"), Verdict)
    assert (found.input_tokens, found.thinking_tokens, found.output_tokens) == (
        USAGE["promptTokenCount"],
        USAGE["thoughtsTokenCount"],
        USAGE["candidatesTokenCount"],
    )


def test_an_answer_asked_again_counts_the_tokens_of_both(waits):
    fake_gemini().answer_next({"verdict": "fine"})

    found = get_llm(LLMUse.VIDEO).answered(Prompt.of("Check"), Verdict)

    assert found.attempts == 2
    assert (found.input_tokens, found.thinking_tokens, found.output_tokens) == (
        2 * USAGE["promptTokenCount"],
        2 * USAGE["thoughtsTokenCount"],
        2 * USAGE["candidatesTokenCount"],
    )


def test_an_outage_stops_at_the_deadline_not_after_every_retry(clock):
    """A check has one deadline: no retry waits past it, and every request
    gets only the time left (the configured 300 s otherwise)."""
    for _ in range(5):  # down for every try `OFM_LLM_RETRIES` 4 allows
        fake_gemini().fail_next(503, "UNAVAILABLE", "The model is overloaded")
    deadline = clock.monotonic() + 30

    with pytest.raises(LLMUnavailableError) as err:
        get_llm(LLMUse.VIDEO).answered(Prompt.of("Check"), Verdict, deadline=deadline)

    assert str(err.value) == "Gemini took too long"
    # 5 s and 10 s fit; the 3rd try's 20 s wait would end past the deadline.
    assert clock.slept == [5.0, 10.0]
    assert fake_gemini().timeouts == [
        pytest.approx(30, abs=1),
        pytest.approx(25, abs=1),
        pytest.approx(15, abs=1),
    ]
    assert "Gemini is unavailable (503)" in err.value.detail


def test_a_deadline_already_passed_sends_nothing(clock):
    with pytest.raises(LLMUnavailableError, match="Gemini took too long"):
        get_llm(LLMUse.VIDEO).answered(
            Prompt.of("Check"), Verdict, deadline=clock.monotonic() - 1
        )
    assert fake_gemini().requests == []


def test_a_big_file_is_uploaded_waited_on_and_deleted_after(waits, media, monkeypatch):
    monkeypatch.setenv("OFM_LLM_INLINE_MAX_BYTES", "100")
    fake_gemini().polls_until_active = 2
    _, video = media

    found = get_llm(LLMUse.VIDEO).ask(Prompt.of("Check", video), Verdict)

    assert found == Verdict(ok=True, problems=[])
    [body] = fake_gemini().requests
    assert _media(body, "inlineData") == []
    [part] = _media(body, "fileData")
    assert _mime(part) == "video/mp4"
    assert waits == [2.0, 2.0]  # PROCESSING twice, then ACTIVE
    assert fake_gemini().files == {}
    assert fake_gemini().deleted == ["files/fake-1"]


def test_a_file_google_cannot_process_is_an_input_error(waits, media, monkeypatch):
    monkeypatch.setenv("OFM_LLM_INLINE_MAX_BYTES", "100")
    fake_gemini().fail_processing()
    with pytest.raises(LLMInputError, match="Gemini could not process clip.mp4"):
        get_llm(LLMUse.VIDEO).ask(Prompt.of("Check", media[1]), Verdict)
    assert fake_gemini().requests == []
    assert fake_gemini().files == {}  # deleted even so


def test_a_rate_limit_waits_googles_hint_then_answers(waits):
    fake_gemini().fail_next(429, "RESOURCE_EXHAUSTED", "Quota exceeded", 7)
    fake_gemini().fail_next(503, "UNAVAILABLE", "The model is overloaded")

    found = get_llm(LLMUse.VIDEO).ask(Prompt.of("Check"), Verdict)

    assert found.ok is True
    assert waits == [7.0, 10.0]  # Google's hint, then 5 s x 2 for the 2nd try
    assert len(fake_gemini().requests) == 3


def test_a_hint_longer_than_the_longest_wait_fails_at_once(waits, monkeypatch):
    """A daily quota used up says "retry in hours": nobody waits for that."""
    monkeypatch.setenv("OFM_LLM_RETRY_MAX_WAIT_S", "30")
    fake_gemini().fail_next(429, "RESOURCE_EXHAUSTED", "Daily quota", 3600)
    with pytest.raises(LLMUnavailableError, match="rate limited or out of free quota"):
        get_llm(LLMUse.VIDEO).ask(Prompt.of("Check"), Verdict)
    assert waits == []
    assert len(fake_gemini().requests) == 1


def test_an_outage_that_outlasts_the_retries_is_unavailable(waits, monkeypatch):
    monkeypatch.setenv("OFM_LLM_RETRIES", "2")
    for _ in range(3):
        fake_gemini().fail_next(500, "INTERNAL", "Internal error")

    with pytest.raises(LLMUnavailableError, match=r"Gemini is unavailable \(500\)"):
        get_llm(LLMUse.VIDEO).ask(Prompt.of("Check"), Verdict)

    assert len(fake_gemini().requests) == 3
    assert waits == [5.0, 10.0]


def test_a_wrong_key_is_never_retried(waits):
    with pytest.raises(LLMAuthError, match="Gemini: wrong API key"):
        get_llm(LLMUse.VIDEO, api_key="not-the-key").ask(Prompt.of("Check"), Verdict)
    assert waits == []


def test_an_unknown_model_is_a_setup_error(waits):
    with pytest.raises(LLMConfigError, match="gemini-0-nope is not found"):
        get_llm(LLMUse.VIDEO, model="gemini-0-nope").ask(Prompt.of("Check"), Verdict)


def test_a_rejected_request_is_an_input_error(waits):
    fake_gemini().fail_next(400, "INVALID_ARGUMENT", "Unsupported MIME type")
    with pytest.raises(LLMInputError, match="rejected the request: Unsupported MIME"):
        get_llm(LLMUse.VIDEO).ask(Prompt.of("Check"), Verdict)


def test_a_safety_block_is_a_refusal(waits):
    fake_gemini().refuse_next()
    with pytest.raises(LLMRefusedError, match=r"refused the prompt \(PROHIBITED"):
        get_llm(LLMUse.VIDEO).ask(Prompt.of("Check"), Verdict)


def test_an_answer_in_another_shape_twice_is_an_output_error(waits):
    fake_gemini().answer_next({"verdict": "fine"})
    fake_gemini().answer_next({"verdict": "fine"})
    with pytest.raises(LLMOutputError, match="Gemini answered in the wrong shape"):
        get_llm(LLMUse.VIDEO).ask(Prompt.of("Check"), Verdict)
    assert len(fake_gemini().requests) == 2  # asked once more first


def test_media_a_provider_does_not_read_is_refused_before_sending(waits, media):
    class TextOnly(GeminiLLM):
        media = frozenset({MediaKind.AUDIO})

    with pytest.raises(LLMInputError, match="Gemini does not read image, video"):
        TextOnly(FAKE_KEY, "gemini-3.8-flash").ask(Prompt.of("Check", *media), Verdict)
    assert fake_gemini().requests == []
