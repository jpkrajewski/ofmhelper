"""Trial captions: watching asks the video chain (with the small proxy),
writing the caption asks the chat provider first -- Gemini's free tier is
20 requests a day per model, Groq's 1,000 -- and the video chain only when
it fails."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from ofmhelpers.features.trials import captions, media
from ofmhelpers.features.trials.captions import CHOICES, Captions, ReelNotes
from ofmhelpers.features.trials.state import CAPTION_MAX
from ofmhelpers.integrations.llm import LLMUnavailableError, LLMUse, MediaKind


class _Model:
    def __init__(self, entry: str, asked: list, answers: dict, *, video: bool):
        self.entry, self.asked, self.answers = entry, asked, answers
        self.media = {MediaKind.VIDEO} if video else set()

    def ask(self, prompt, _answer):
        self.asked.append((self.entry, [p.path.name for p in prompt.media]))
        found = self.answers[self.entry]
        if isinstance(found, Exception):
            raise found
        return found


@pytest.fixture
def models(monkeypatch):
    """chat = groq (text only), video chain = two Gemini models; `answers`
    per entry, every ask recorded as (entry, media file names)."""
    asked: list = []
    answers: dict = {}
    monkeypatch.setattr(captions.settings.llm, "default_chat", "groq", raising=False)
    monkeypatch.setattr(
        type(captions.settings.llm), "video_chain", property(lambda _s: ["g1", "g2"])
    )

    def get_llm(use, provider):
        return _Model(provider, asked, answers, video=use is LLMUse.VIDEO)

    monkeypatch.setattr(captions, "get_llm", get_llm)
    monkeypatch.setattr(media, "ai_proxy", lambda src: src.with_suffix(".ai.mp4"))
    return asked, answers


def test_the_caption_is_written_by_the_chat_model(models):
    asked, answers = models
    answers["groq"] = Captions(captions=["  just here for my checkup ", "second"])

    assert captions.captions_for(ReelNotes(description="d")) == [
        "just here for my checkup",
        "second",
    ]
    assert asked == [("groq", [])]


def test_the_video_chain_writes_it_when_the_chat_model_is_down(models):
    asked, answers = models
    answers["groq"] = LLMUnavailableError("429")
    answers["g1"] = Captions(captions=["from gemini"])

    assert captions.captions_for(ReelNotes()) == ["from gemini"]
    assert [entry for entry, _media in asked] == ["groq", "g1"]


def test_one_ask_asks_for_all_the_choices(models, monkeypatch):
    """Five asks would cost five of the day's requests."""
    _asked, answers = models
    answers["groq"] = Captions(captions=["one"])
    prompts = []
    ask = _Model.ask

    def recording(self, prompt, answer):
        prompts.append(prompt)
        return ask(self, prompt, answer)

    monkeypatch.setattr(_Model, "ask", recording)

    captions.captions_for(ReelNotes(description="a walk"))

    assert len(prompts) == 1
    assert f"Write {CHOICES} different" in prompts[0].parts[0].text
    assert "a walk" in prompts[0].parts[0].text


def test_the_choices_come_back_clean_fitting_and_none_twice():
    found = Captions(
        captions=[
            '"quoted  one"',
            "QUOTED ONE",
            "",
            "   ",
            "x" * 200,
            "two\nlines",
            "three",
            "four",
            "five",
        ]
    ).captions

    assert len(found) == CHOICES
    assert found[0] == "QUOTED ONE"  # same words: kept once, in its first place
    assert found[1] == "x" * CAPTION_MAX
    assert found[2] == "two lines"


@pytest.mark.parametrize("empty", [[], ["", "   ", '""']])
def test_no_caption_at_all_is_the_wrong_answer(empty):
    """The chain then asks the next model."""
    with pytest.raises(ValidationError):
        Captions(captions=empty)


def test_watching_sends_the_proxy_to_the_video_chain_only(models):
    asked, answers = models
    answers["g1"] = LLMUnavailableError("quota")
    answers["g2"] = ReelNotes(transcript="hi", description="a walk")

    notes = captions.understand(Path("KEY-VID_1.mp4"))

    assert notes.description == "a walk"
    assert asked == [("g1", ["KEY-VID_1.ai.mp4"]), ("g2", ["KEY-VID_1.ai.mp4"])]
