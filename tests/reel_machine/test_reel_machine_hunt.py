"""
The second pass: Gemini's analysis in, search terms out (reel_machine/hunt.py).

Everything here is about it being *optional*. The reel is already downloaded
and analyzed by the time this runs, so no failure of the free model may cost
the job -- no key, an HTTP error, a non-JSON answer and a wrong-shaped answer
all have to come back empty rather than raise.
"""

import json
from pathlib import Path
from unittest import mock

import pytest

from ofmhelpers.reel_machine import hunt
from ofmhelpers.reel_machine.schema import ReelAnalysis

EXAMPLE = Path(__file__).parent / "example.json"


@pytest.fixture
def analysis() -> ReelAnalysis:
    return ReelAnalysis.model_validate(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def _groq_answer(payload: dict):
    response = mock.Mock()
    response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(payload)}}]
    }
    return response


def test_no_api_key_means_no_suggestions(analysis, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with mock.patch.object(hunt.requests, "post") as post:
        ideas = hunt.suggest_hunt(analysis)

    assert ideas.is_empty
    post.assert_not_called()


def test_tags_queries_and_outfits_come_back_cleaned(analysis, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    answer = _groq_answer(
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
    with mock.patch.object(hunt.requests, "post", return_value=answer):
        ideas = hunt.suggest_hunt(analysis)

    assert ideas.instagram_topics == ["starbucks-girl", "starbucks-skit"]
    assert ideas.search_queries == ["starbucks barista skit"]
    assert ideas.outfit_ideas == ["oversized green apron fit"]


def test_the_model_is_given_what_the_reel_is_not_the_shot_list(analysis, monkeypatch):
    """scene_events/shots describe how to film it, which says nothing about
    what to search for -- only the "what is this" fields are sent."""
    monkeypatch.setenv("GROQ_API_KEY", "k")
    with mock.patch.object(
        hunt.requests, "post", return_value=_groq_answer({})
    ) as post:
        hunt.suggest_hunt(analysis)

    sent = post.call_args.kwargs["json"]["messages"][1]["content"]
    assert analysis.viral_factor in sent
    assert "scene_events" not in sent
    assert "camera_behavior" not in sent


def test_a_failed_call_is_swallowed(analysis, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    with mock.patch.object(hunt.requests, "post", side_effect=OSError("no network")):
        assert hunt.suggest_hunt(analysis).is_empty


def test_a_non_json_answer_is_swallowed(analysis, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    response = mock.Mock()
    response.json.return_value = {"choices": [{"message": {"content": "sure! here:"}}]}
    with mock.patch.object(hunt.requests, "post", return_value=response):
        assert hunt.suggest_hunt(analysis).is_empty


def test_a_wrong_shaped_answer_yields_empty_lists(analysis, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    answer = _groq_answer({"instagram_topics": "starbucks-girl", "search_queries": 3})
    with mock.patch.object(hunt.requests, "post", return_value=answer):
        assert hunt.suggest_hunt(analysis).is_empty


def test_an_unvalidated_analysis_is_not_sent_anywhere(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    with mock.patch.object(hunt.requests, "post") as post:
        assert hunt.suggest_hunt(None).is_empty
    post.assert_not_called()


def test_only_the_main_subjects_wardrobe_is_sent(analysis, monkeypatch):
    """The other people are the cameraman and passers-by; their wardrobe
    ("not visible") produced outfit ideas for nobody."""
    monkeypatch.setenv("GROQ_API_KEY", "k")
    with mock.patch.object(
        hunt.requests, "post", return_value=_groq_answer({})
    ) as post:
        hunt.suggest_hunt(analysis)

    sent = post.call_args.kwargs["json"]["messages"][1]["content"]
    assert analysis.subject.wardrobe[:40] in sent
    assert "not visible" not in sent
