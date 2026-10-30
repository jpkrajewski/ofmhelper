"""Trial captions: watching asks the video chain (with the small proxy),
writing the caption asks the chat provider first -- Gemini's free tier is
20 requests a day per model, Groq's 1,000 -- and the video chain only when
it fails."""

from pathlib import Path

import pytest

from ofmhelpers.llm import LLMUnavailableError, LLMUse, MediaKind
from ofmhelpers.trials import captions, media
from ofmhelpers.trials.captions import Caption, ReelNotes


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
    answers["groq"] = Caption(caption="  just here for my checkup ")

    assert captions.caption_for(ReelNotes(description="d"), used=[]) == (
        "just here for my checkup"
    )
    assert asked == [("groq", [])]


def test_the_video_chain_writes_it_when_the_chat_model_is_down(models):
    asked, answers = models
    answers["groq"] = LLMUnavailableError("429")
    answers["g1"] = Caption(caption="from gemini")

    assert captions.caption_for(ReelNotes(), used=["old"]) == "from gemini"
    assert [entry for entry, _media in asked] == ["groq", "g1"]


def test_watching_sends_the_proxy_to_the_video_chain_only(models):
    asked, answers = models
    answers["g1"] = LLMUnavailableError("quota")
    answers["g2"] = ReelNotes(transcript="hi", description="a walk")

    notes = captions.understand(Path("KEY-VID_1.mp4"))

    assert notes.description == "a walk"
    assert asked == [("g1", ["KEY-VID_1.ai.mp4"]), ("g2", ["KEY-VID_1.ai.mp4"])]
