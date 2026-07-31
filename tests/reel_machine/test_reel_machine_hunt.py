"""
The second pass: Gemini's analysis in, search terms out (reel_machine/hunt.py).

Everything here is about it being *optional*. The reel is already downloaded
and analyzed by the time this runs, so no failure of the text model may cost
the job -- no provider configured, a transport error, a non-JSON answer and a
wrong-shaped answer all have to come back empty rather than raise.

The HTTP call itself belongs to the provider now
(test_reel_machine_groq_provider.py); what is stubbed here is the capability,
not requests.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

from ofmhelpers.reel_machine import hunt
from ofmhelpers.reel_machine.models import ReelAnalysis

EXAMPLE = Path(__file__).parent / "example.json"


@pytest.fixture
def analysis() -> ReelAnalysis:
    return ReelAnalysis.model_validate(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def _provider(payload: dict | str):
    """A stub TextLLMProvider answering with `payload`."""
    provider = mock.Mock()
    provider.complete_json.return_value = (
        payload if isinstance(payload, str) else json.dumps(payload)
    )
    return provider


@pytest.fixture
def text_provider(monkeypatch):
    """Installs a stub provider and hands it back, so a test can assert on the
    prompt it was given."""

    def install(payload: dict | str | None = None):
        provider = _provider({} if payload is None else payload)
        monkeypatch.setattr(hunt, "get_text_provider", mock.Mock(return_value=provider))
        return provider

    return install


def test_no_provider_configured_means_no_suggestions(analysis, monkeypatch):
    """get_text_provider answers None when there is no key -- pass 2 is a
    nice-to-have, so that is a skip, not a failure."""
    monkeypatch.setattr(hunt, "get_text_provider", mock.Mock(return_value=None))

    assert hunt.suggest_hunt(analysis).is_empty


def test_tags_queries_and_outfits_come_back_cleaned(analysis, text_provider):
    text_provider(
        {
            # /popular/<slug> pages are hyphenated words; a model still
            # answers with capitals, '#' and spaces, so slugs are normalized.
            "instagram_topics": [
                "#Starbucks Girl!",
                "starbucks-skit",
                "starbucks-skit",
            ],
            "search_queries": ["starbucks  barista skit", ""],
            "outfit_ideas": ["oversized green apron fit"],
        }
    )

    ideas = hunt.suggest_hunt(analysis)

    assert ideas.instagram_topics == ["starbucks-girl", "starbucks-skit"]
    assert ideas.search_queries == ["starbucks barista skit"]
    assert ideas.outfit_ideas == ["oversized green apron fit"]


def test_the_model_is_given_what_the_reel_is_not_the_shot_list(analysis, text_provider):
    """scene_events/shots describe how to film it, which says nothing about
    what to search for -- only the "what is this" fields are sent."""
    provider = text_provider()

    hunt.suggest_hunt(analysis)

    sent = provider.complete_json.call_args.args[0]
    assert analysis.viral_factor in sent
    assert "scene_events" not in sent
    assert "camera_behavior" not in sent


def test_a_failed_call_is_swallowed(analysis, text_provider):
    provider = text_provider()
    provider.complete_json.side_effect = OSError("no network")

    assert hunt.suggest_hunt(analysis).is_empty


def test_a_non_json_answer_is_swallowed(analysis, text_provider):
    text_provider("sure! here:")

    assert hunt.suggest_hunt(analysis).is_empty


def test_a_wrong_shaped_answer_yields_empty_lists(analysis, text_provider):
    text_provider({"instagram_topics": "starbucks-girl", "search_queries": 3})

    assert hunt.suggest_hunt(analysis).is_empty


def test_an_unvalidated_analysis_is_not_sent_anywhere(text_provider):
    provider = text_provider()

    assert hunt.suggest_hunt(None).is_empty
    provider.complete_json.assert_not_called()


def test_only_the_main_subjects_wardrobe_is_sent(analysis, text_provider):
    """The other people are the cameraman and passers-by; their wardrobe
    ("not visible") produced outfit ideas for nobody."""
    provider = text_provider()

    hunt.suggest_hunt(analysis)

    sent = provider.complete_json.call_args.args[0]
    assert analysis.subject.wardrobe[:40] in sent
    assert "not visible" not in sent
