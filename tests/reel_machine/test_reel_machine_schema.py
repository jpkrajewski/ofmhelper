"""
schema.parse_analysis is the only gate between raw model output and a prompt
we hand to Seedance, so the failure modes it has to catch are worth pinning
down: fenced JSON, prose around the JSON, and half-filled objects.
"""

import json

import pytest

from ofmhelpers.reel_machine.prompts import ANALYSIS_PROMPT
from ofmhelpers.reel_machine.schema import (
    REQUIRED_KEYS,
    AnalysisError,
    parse_analysis,
    strip_code_fence,
)


def _valid_payload(**overrides) -> dict:
    payload = {
        key: [] if key in ("people", "scene_events", "imperfections", "shots") else "x"
        for key in REQUIRED_KEYS
    }
    payload.update(overrides)
    return payload


def test_parses_a_complete_response():
    data = parse_analysis(json.dumps(_valid_payload(environment="a lift lobby")))
    assert data["environment"] == "a lift lobby"
    assert data["scene_events"] == []


def test_strips_a_markdown_fence_the_prompt_asked_the_model_not_to_add():
    raw = "```json\n" + json.dumps(_valid_payload()) + "\n```"
    assert parse_analysis(raw)["format"] == "x"


def test_strips_prose_around_the_json():
    raw = "Here is the JSON:\n" + json.dumps(_valid_payload()) + "\nHope that helps!"
    assert parse_analysis(raw)["style"] == "x"


def test_strip_code_fence_leaves_bare_json_alone():
    raw = json.dumps({"a": 1})
    assert strip_code_fence(raw) == raw


def test_rejects_non_json():
    with pytest.raises(AnalysisError, match="valid JSON"):
        parse_analysis("I can't analyze that video, sorry.")


def test_rejects_a_json_array():
    with pytest.raises(AnalysisError, match="expected a JSON object"):
        parse_analysis("[1, 2, 3]")


def test_rejects_a_response_missing_keys():
    payload = _valid_payload()
    del payload["scene_events"]
    del payload["negative_prompt"]
    with pytest.raises(AnalysisError, match="missing required keys") as exc:
        parse_analysis(json.dumps(payload))
    assert "scene_events" in str(exc.value)
    assert "negative_prompt" in str(exc.value)


def test_rejects_a_scalar_where_a_list_was_asked_for():
    with pytest.raises(AnalysisError, match="expected a list"):
        parse_analysis(json.dumps(_valid_payload(scene_events="0:00 she talks")))


def test_prompt_asks_for_every_key_the_schema_requires():
    """If the two ever drift, every job fails validation -- cheaper to catch
    here than in production."""
    for key in REQUIRED_KEYS:
        assert f'"{key}"' in ANALYSIS_PROMPT
