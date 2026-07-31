"""
ReelAnalysis.from_llm_text is the only gate between raw model output and a prompt
we hand to Seedance, so the failure modes it has to catch are worth pinning
down: fenced JSON, prose around the JSON, half-filled objects, and wrong
types. Every failure has to carry the raw text -- the caller shows it to the
user instead of failing the job.
"""

import json
import re
from pathlib import Path

import pytest

from ofmhelpers.reel_machine.models import (
    REQUIRED_KEYS,
    AnalysisError,
    Person,
    ReelAnalysis,
    SceneEvent,
    Shot,
)
from ofmhelpers.reel_machine.prompts import DEFAULT_ANALYSIS_PROMPT

EXAMPLE = Path(__file__).parent / "example.json"


def _valid_payload(**overrides) -> dict:
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    payload.update(overrides)
    return payload


def test_parses_a_complete_response():
    data = ReelAnalysis.from_llm_text(
        json.dumps(_valid_payload(environment="a lift lobby"))
    )
    assert data.environment == "a lift lobby"
    assert data.scene_events[0].speaker == "subject"


def test_parses_a_real_gemini_response():
    """example.json is verbatim output from a real Gemini run on a real reel.
    The edited payloads above only prove the validator's rules; this proves
    the rules match what the model actually returns."""
    data = ReelAnalysis.from_llm_text(EXAMPLE.read_text(encoding="utf-8"))

    assert data.people[0].id == "subject"
    # The prompt asks for silent moments as line/delivery null with the action
    # still filled in -- a validator that rejected nulls would fail real jobs.
    silent = data.scene_events[-1]
    assert silent.line is None
    assert silent.delivery is None
    assert silent.action


def test_rejects_keys_the_prompt_never_asked_for():
    """The prompt spells out the exact object it wants, so a key nobody asked
    for means the model went off-script -- show the raw answer rather than a
    shape the rest of the app half-understands."""
    payload = _valid_payload()
    payload["people"][0]["accent"] = "latino woman accent"
    with pytest.raises(AnalysisError, match="people.0.accent"):
        ReelAnalysis.from_llm_text(json.dumps(payload))


def test_strips_a_markdown_fence_the_prompt_asked_the_model_not_to_add():
    raw = "```json\n" + json.dumps(_valid_payload(format="x")) + "\n```"
    assert ReelAnalysis.from_llm_text(raw).format == "x"


def test_strips_prose_around_the_json():
    raw = "Here is the JSON:\n" + json.dumps(_valid_payload(style="x")) + "\nEnjoy!"
    assert ReelAnalysis.from_llm_text(raw).style == "x"


def test_strip_code_fence_leaves_bare_json_alone():
    raw = json.dumps({"a": 1})
    assert ReelAnalysis.strip_code_fence(raw) == raw


def test_rejects_non_json():
    with pytest.raises(AnalysisError, match="valid JSON"):
        ReelAnalysis.from_llm_text("I can't analyze that video, sorry.")


def test_rejects_a_json_array():
    with pytest.raises(AnalysisError, match="expected a JSON object"):
        ReelAnalysis.from_llm_text("[1, 2, 3]")


def test_rejects_a_response_missing_keys():
    payload = _valid_payload()
    del payload["scene_events"]
    del payload["negative_prompt"]
    with pytest.raises(AnalysisError, match="doesn't match") as exc:
        ReelAnalysis.from_llm_text(json.dumps(payload))
    assert "scene_events" in str(exc.value)
    assert "negative_prompt" in str(exc.value)


def test_rejects_a_scalar_where_a_list_was_asked_for():
    with pytest.raises(AnalysisError, match="scene_events"):
        ReelAnalysis.from_llm_text(
            json.dumps(_valid_payload(scene_events="0:00 she talks"))
        )


def test_rejects_a_malformed_nested_entry():
    payload = _valid_payload()
    del payload["shots"][0]["camera_behavior"]
    with pytest.raises(AnalysisError, match=r"shots\.0\.camera_behavior"):
        ReelAnalysis.from_llm_text(json.dumps(payload))


def test_rejects_a_shot_cue_that_digests_several_scene_events():
    """The tell that a weaker model collapsed the whole clip into one summary
    shot instead of breaking it down: the cue stops being a timestamp and
    becomes a dialogue digest. Better to show the raw answer than to hand
    Seedance a one-shot timeline."""
    payload = _valid_payload()
    payload["shots"][0]["scene_event_cue"] = (
        "0:00 - person_2: 'Here, your frappuccino's ready.'; 0:01 - subject: 'Thanks.'"
    )
    with pytest.raises(AnalysisError, match=r"shots\.0\.scene_event_cue"):
        ReelAnalysis.from_llm_text(json.dumps(payload))


def test_accepts_a_bare_timestamp_cue_or_none():
    payload = _valid_payload()
    payload["shots"][0]["scene_event_cue"] = None
    assert (
        ReelAnalysis.from_llm_text(json.dumps(payload)).shots[0].scene_event_cue is None
    )
    assert (
        ReelAnalysis.from_llm_text(json.dumps(_valid_payload()))
        .shots[1]
        .scene_event_cue
        == "0:02"
    )


def test_validation_is_strict_about_types():
    """No "3" -> 3 coercion: a wrong type means the model answered wrong, and
    the caller shows the raw text rather than a half-guessed prompt."""
    with pytest.raises(AnalysisError, match="atmosphere"):
        ReelAnalysis.from_llm_text(json.dumps(_valid_payload(atmosphere=7)))


def test_every_error_carries_the_raw_response():
    """The whole point of the raw fallback -- a caller with no text to show
    is back to a dead job."""
    raw = "sorry, I can't help with that"
    with pytest.raises(AnalysisError) as exc:
        ReelAnalysis.from_llm_text(raw)
    assert exc.value.raw == raw


def test_elevenlabs_prompt_is_only_the_subject_speaking():
    data = ReelAnalysis.from_llm_text(EXAMPLE.read_text(encoding="utf-8"))
    speech = data.elevenlabs_ready_prompt_from_subject()

    assert speech.startswith("[playful, pleading tone] Babe")
    assert " [pause] " in speech
    # person_2's dialogue and the subject's silent moments contribute nothing.
    assert "Where, there?" not in speech
    assert speech.count("[pause]") == 2
    assert not speech.endswith("[pause]")


def test_elevenlabs_prompt_can_read_another_speaker():
    data = ReelAnalysis.from_llm_text(EXAMPLE.read_text(encoding="utf-8"))
    assert (
        data.elevenlabs_ready_prompt_from_subject("person_2")
        == "[curious off-camera voice] Where, there? [pause] "
        "[questioning tone] The D?"
    )


def test_elevenlabs_prompt_falls_back_to_the_bare_line():
    data = ReelAnalysis.model_validate(
        _valid_payload(
            scene_events=[
                {
                    "timestamp": "0:00",
                    "speaker": "subject",
                    "line": "hi",
                    "delivery": None,
                    "action": "waves",
                    "pose": "arm raised mid-wave",
                    "facial_expression": "grinning",
                }
            ]
        )
    )
    assert data.elevenlabs_ready_prompt_from_subject() == "hi"


def test_prompt_asks_for_every_key_the_schema_requires():
    """If the two ever drift, every job fails validation -- cheaper to catch
    here than in production."""
    for key in REQUIRED_KEYS:
        assert f'"{key}"' in DEFAULT_ANALYSIS_PROMPT


def test_prompt_asks_for_every_scene_event_key_too():
    """REQUIRED_KEYS only covers ReelAnalysis' own fields, so the nested
    sections drift silently -- extra="forbid" then rejects a real answer for a
    key the prompt never mentioned (or omits one the schema requires)."""
    for key in SceneEvent.model_fields:
        assert f'"{key}"' in DEFAULT_ANALYSIS_PROMPT


def test_the_prompt_never_names_a_field_the_schema_does_not_have():
    """The drift that got through the two tests above: prose in the prompt
    instructing the model about `face_expression`, a field that does not
    exist. Constrained decoding hides it; the instruction is still nonsense."""
    known = {
        *ReelAnalysis.model_fields,
        *Person.model_fields,
        *SceneEvent.model_fields,
        *Shot.model_fields,
    }
    # snake_case words the prompt mentions that look like field names
    mentioned = set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", DEFAULT_ANALYSIS_PROMPT))
    unknown = {
        word
        for word in mentioned
        # near-miss of a real field name -- e.g. face_expression vs
        # facial_expression -- rather than an ordinary phrase like "no gaps"
        if word not in known and any(word.endswith(f.split("_")[-1]) for f in known)
    }
    assert not unknown


def test_pose_and_expression_are_null_for_anyone_off_camera():
    """Unlike `action`, these two are optional: the prompt asks for null when
    the speaker is off camera or isn't the main subject, so a real answer has
    them missing on most person_2 entries."""
    payload = _valid_payload()
    payload["scene_events"][0]["pose"] = None
    del payload["scene_events"][0]["facial_expression"]

    event = ReelAnalysis.from_llm_text(json.dumps(payload)).scene_events[0]
    assert event.pose is None
    assert event.facial_expression is None
