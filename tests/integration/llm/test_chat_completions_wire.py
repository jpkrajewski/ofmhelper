"""Groq and Cerebras through `ofmhelpers.llm` (one OpenAI-style
`/chat/completions` client, llm/chat_completions.py) with each vendor faked
at the wire: images inline and shrunk, a video as a contact sheet, the
strict schema sent, the image cap held before sending, retries, and every
failure as the error the caller gets."""

import base64
import io
import json
import time
from pathlib import Path

import pytest
from PIL import Image
from pydantic import BaseModel

from ofmhelpers.llm import (
    LLMAuthError,
    LLMConfigError,
    LLMInputError,
    LLMOutputError,
    LLMRefusedError,
    LLMUnavailableError,
    LLMUse,
    Prompt,
    get_llm,
)
from ofmhelpers.llm import base as llm_base
from ofmhelpers.llm.cerebras.fake import FAKE_KEY as CEREBRAS_KEY
from ofmhelpers.llm.cerebras.fake import fake_cerebras
from ofmhelpers.llm.chat_completions_fake import USAGE, FakeChatCompletions
from ofmhelpers.llm.groq.fake import FAKE_KEY as GROQ_KEY
from ofmhelpers.llm.groq.fake import fake_groq
from ofmhelpers.utils import fake_media

VENDORS = {
    "groq": ("GROQ_API_KEY", GROQ_KEY, fake_groq, "Groq", 3),
    "cerebras": ("CEREBRAS_API_KEY", CEREBRAS_KEY, fake_cerebras, "Cerebras", 2),
}


class Verdict(BaseModel):
    ok: bool
    problems: list[str]


class _Clock:
    """A sleep is noted and moves the clock on, never waited."""

    def __init__(self) -> None:
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return time.monotonic() + sum(self.slept)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


@pytest.fixture(params=list(VENDORS))
def vendor(request, fake_externals, monkeypatch) -> str:
    """Each vendor on its fake, as the chat default."""
    env, key, *_ = VENDORS[request.param]
    monkeypatch.setenv(env, key)
    monkeypatch.setenv("LLM_DEFAULT_CHAT", request.param)
    return request.param


@pytest.fixture
def fake(vendor) -> FakeChatCompletions:
    return VENDORS[vendor][2]()


@pytest.fixture
def label(vendor) -> str:
    return VENDORS[vendor][3]


@pytest.fixture
def clock(vendor, monkeypatch) -> _Clock:
    found = _Clock()
    monkeypatch.setattr(llm_base, "time", found)
    return found


@pytest.fixture
def waits(clock) -> list[float]:
    return clock.slept


def _ask(prompt: Prompt) -> Verdict:
    return get_llm(LLMUse.CHAT).ask(prompt, Verdict)


def _content(body: dict) -> list[dict]:
    return body["messages"][-1]["content"]


def _image(part: dict) -> Image.Image:
    url = part["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))


def test_text_and_images_go_inline_with_a_strict_schema(fake, tmp_path):
    photo = tmp_path / "subject.png"
    Image.new("RGB", (3000, 2000), "teal").save(photo)
    fake.answer_next({"ok": False, "problems": ["face changes"]})

    found = _ask(Prompt.of("Subject:", photo, system="You check reels."))

    assert found == Verdict(ok=False, problems=["face changes"])
    [body] = fake.requests
    text, image = _content(body)
    assert text == {"type": "text", "text": "Subject:"}
    assert max(_image(image).size) == 1024  # shrunk: tokens grow with size
    strict = body["response_format"]["json_schema"]
    assert strict["strict"] is True
    assert strict["schema"]["additionalProperties"] is False
    assert body["temperature"] == 0  # the vendors' default here: a checker
    assert fake.timeouts == [300.0]  # OFM_LLM_TIMEOUT_S


def test_the_answer_says_the_tokens_the_vendor_counted(fake):
    found = get_llm(LLMUse.CHAT).answered(Prompt.of("Check"), Verdict)
    assert (found.input_tokens, found.thinking_tokens, found.output_tokens) == (
        USAGE["prompt_tokens"],
        None,  # counted inside completion_tokens, not apart
        USAGE["completion_tokens"],
    )


def test_an_outage_stops_at_the_deadline_not_after_every_retry(fake, label, clock):
    for _ in range(5):
        fake.fail_next(503, "Service unavailable")
    deadline = clock.monotonic() + 20

    with pytest.raises(LLMUnavailableError) as err:
        get_llm(LLMUse.CHAT).answered(Prompt.of("Check"), Verdict, deadline=deadline)

    assert str(err.value) == f"{label} took too long"
    assert f"{label} is unavailable (503)" in err.value.detail
    # 5 s and 10 s fit; the 3rd try's 20 s wait would end past the deadline.
    assert clock.slept == [5.0, 10.0]
    assert fake.timeouts == [
        pytest.approx(20, abs=1),
        pytest.approx(15, abs=1),
        pytest.approx(5, abs=1),
    ]


def test_the_model_is_shown_the_schema_it_must_answer_in(fake):
    """Groq checks the answer after writing it: a model never shown the
    field names made up its own, and only "no glitches" got through."""
    _ask(Prompt.of("Check", system="You check reels."))

    [body] = fake.requests
    system = body["messages"][0]
    assert system["role"] == "system"
    rules, shape = system["content"].split("\n\n", 1)
    assert rules == "You check reels."
    lead, schema = shape.split("\n", 1)
    assert lead.startswith("Reply with one JSON object that follows this JSON Schema")
    assert json.loads(schema) == body["response_format"]["json_schema"]["schema"]


def test_a_video_goes_as_a_note_and_one_contact_sheet(fake, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(fake_media.mp4())

    _ask(Prompt.of("Check this reel:", video))

    [body] = fake.requests
    lead, note, sheet = _content(body)
    assert lead["text"] == "Check this reel:"
    assert note["text"].startswith("The next image is the video clip.mp4 as 9 evenly")
    # 3x3 portrait stills (1080x1920), shrunk like any image.
    assert _image(sheet).size == (576, 1024)


def test_more_images_than_the_vendor_takes_are_refused_before_sending(
    vendor, fake, label, tmp_path
):
    cap = VENDORS[vendor][4]
    photos = []
    for i in range(cap + 1):
        photos.append(tmp_path / f"p{i}.png")
        Image.new("RGB", (32, 32), "red").save(photos[-1])

    with pytest.raises(LLMInputError, match=f"{label} takes at most {cap} images"):
        _ask(Prompt.of("Compare", *photos))
    assert fake.requests == []


def test_a_rate_limit_waits_the_vendors_hint_then_answers(fake, waits):
    fake.fail_next(429, "Rate limit reached", retry_after_s=4)
    fake.fail_next(503, "Service unavailable")

    assert _ask(Prompt.of("Check")).ok is True
    assert waits == [4.0, 10.0]


def test_a_wrong_key_is_an_auth_error(vendor, label, waits):
    with pytest.raises(LLMAuthError, match=f"{label}: wrong API key"):
        get_llm(LLMUse.CHAT, api_key="not-the-key").ask(Prompt.of("Check"), Verdict)
    assert waits == []


def test_an_unknown_model_is_a_setup_error(vendor, waits):
    with pytest.raises(LLMConfigError, match="does not exist"):
        get_llm(LLMUse.CHAT, model="no-such-model").ask(Prompt.of("Check"), Verdict)


def test_a_content_filter_is_a_refusal(fake, label):
    fake.filter_next()
    with pytest.raises(LLMRefusedError, match=f"{label} refused to answer"):
        _ask(Prompt.of("Check"))


def test_an_answer_in_another_shape_is_asked_once_more(fake):
    fake.answer_next({"verdict": "fine"})
    fake.answer_next({"ok": False, "problems": ["face changes"]})

    assert _ask(Prompt.of("Check")) == Verdict(ok=False, problems=["face changes"])
    assert len(fake.requests) == 2


def test_an_answer_in_another_shape_twice_is_an_output_error(fake, label):
    fake.answer_next({"verdict": "fine"})
    fake.answer_next({"verdict": "fine"})
    with pytest.raises(LLMOutputError, match=f"{label} answered in the wrong shape"):
        _ask(Prompt.of("Check"))
    assert len(fake.requests) == 2


def test_an_outage_that_outlasts_the_retries_is_unavailable(
    fake, label, waits, monkeypatch
):
    monkeypatch.setenv("OFM_LLM_RETRIES", "1")
    fake.fail_next(500, "boom")
    fake.fail_next(502, "boom")
    with pytest.raises(LLMUnavailableError, match=rf"{label} is unavailable \(502\)"):
        _ask(Prompt.of("Check"))
    assert len(fake.requests) == 2


def test_an_unreadable_image_is_an_input_error(vendor, fake, tmp_path):
    with pytest.raises(LLMInputError, match="Could not read gone.jpg"):
        _ask(Prompt.of("Check", Path(tmp_path / "gone.jpg")))
    assert fake.requests == []


def test_an_answer_failing_the_vendors_schema_check_is_an_output_error(fake, label):
    fake.misanswer_next("Hola")
    fake.misanswer_next("Hola")
    with pytest.raises(
        LLMOutputError, match=f"{label} answered in the wrong shape"
    ) as err:
        _ask(Prompt.of("Say hi"))
    assert "generated: Hola" in err.value.detail


def test_groq_asks_qwen_not_to_think(fake_externals, monkeypatch):
    """At the default effort Groq's qwen answered a schema request in plain
    text (2026-10-25); thinking would also spend the free 8K tokens/min."""
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    get_llm(provider="groq").ask(Prompt.of("Check"), Verdict)
    [body] = fake_groq().requests
    assert body["reasoning_effort"] == "none"
