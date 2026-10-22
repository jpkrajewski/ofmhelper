"""scripts/sync_kie_catalog.py turned offline: a kie.ai docs page (its
OpenAPI spec) in, a catalog entry -- or the reason it is skipped -- out."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[4] / "scripts/sync_kie_catalog.py"


@pytest.fixture(scope="module")
def sync():
    spec = importlib.util.spec_from_file_location("sync_kie_catalog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _page_text(input_schema, model="acme/v1", path="/api/v1/jobs/createTask"):
    spec = {
        "openapi": "3.0.1",
        "paths": {
            path: {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "properties": {
                                        "model": {"type": "string", "enum": [model]},
                                        "input": input_schema,
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
    }
    result = '{"resultUrls":["https://x/1.png"]}'
    return f"# Page\n\n```yaml\n{yaml.safe_dump(spec)}\n```\n{result}\n"


def _entry(sync, monkeypatch, text, current=None):
    monkeypatch.setattr(sync, "_get", lambda url: text)
    page = sync.Page(
        kind="video", family="Acme", title="Acme - Image to Video", url="u"
    )
    return sync._entry(page, current or {})


PROMPT = {"type": "string", "pattern": "^.*\\S.*$", "maxLength": 100}


def test_a_page_becomes_a_catalog_entry(sync, monkeypatch):
    entry = _entry(
        sync,
        monkeypatch,
        _page_text(
            {
                "type": "object",
                "required": ["prompt", "image_url"],
                "properties": {
                    "prompt": {**PROMPT, "example": "x", "x-apidog-mock": "y"},
                    "image_url": {"type": "string", "description": "First frame"},
                    "duration": {"enum": [5, 10], "default": 5},
                    "seed": {"type": "integer", "nullable": True},
                    "old_size": {"type": "string", "deprecated": True},
                    "model": {"type": "string", "enum": ["fast", "pro"]},
                    "nsfw_checker": {"type": "boolean"},
                },
            }
        ),
    )

    assert entry["model_id"] == "acme/v1"
    assert entry["label"] == "Acme · Image to Video"
    assert entry["fixed"] == {"model": "fast", "nsfw_checker": False}
    props = entry["input_schema"]["properties"]
    assert props["prompt"] == {"type": "string", "maxLength": 100}
    assert props["image_url"] == {
        "type": "string",
        "description": "First frame",
        "x-media": "image",
        "format": "uri",
    }
    assert props["duration"] == {"enum": [5, 10], "default": 5, "type": "integer"}
    assert props["seed"]["type"] == ["integer", "null"]
    assert not {"old_size", "model", "nsfw_checker"} & set(props)
    assert entry["input_schema"]["additionalProperties"] is False


def test_media_kind_comes_from_the_field_name(sync):
    uri_list = {"type": "array", "items": {"type": "string", "format": "uri"}}
    kinds = {
        name: sync._media_kind(name, uri_list)
        for name in (
            "video_urls",
            "first_clip_url",
            "reference_voice",
            "driving_audio_url",
            "input_urls",
            "files_url",
            "mask_url",
            "reference_link_urls",
        )
    }

    assert kinds == {
        "video_urls": "video",
        "first_clip_url": "video",
        "reference_voice": "audio",
        "driving_audio_url": "audio",
        "input_urls": "image",
        "files_url": "image",
        "mask_url": "image",
        "reference_link_urls": None,
    }


def test_input_variants_merge_into_one_form(sync, monkeypatch):
    one = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 1}
    two = {**one, "minItems": 2, "maxItems": 2}
    entry = _entry(
        sync,
        monkeypatch,
        _page_text(
            {
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["prompt", "image_urls"],
                        "properties": {"prompt": PROMPT, "image_urls": one},
                    },
                    {
                        "type": "object",
                        "required": ["prompt", "image_urls", "mode"],
                        "properties": {
                            "prompt": PROMPT,
                            "image_urls": two,
                            "mode": {"type": "string", "enum": ["a"]},
                        },
                    },
                ]
            }
        ),
    )

    schema = entry["input_schema"]
    assert schema["required"] == ["image_urls", "prompt"]
    assert schema["properties"]["image_urls"]["minItems"] == 1
    assert schema["properties"]["image_urls"]["maxItems"] == 2
    assert len(schema["anyOf"]) == 2
    assert "mode" in schema["properties"]


def test_an_existing_entry_keeps_its_hand_settings(sync, monkeypatch):
    current = {
        "acme/v1": {
            "model_id": "acme/v1",
            "label": "Acme (hand)",
            "family": "Hand",
            "kind": "video",
            "fixed": {"sound": True},
            "default_prompt": "hello",
        }
    }
    entry = _entry(
        sync,
        monkeypatch,
        _page_text(
            {
                "type": "object",
                "properties": {"prompt": PROMPT, "sound": {"type": "boolean"}},
            }
        ),
        current,
    )

    assert (entry["label"], entry["family"]) == ("Acme (hand)", "Hand")
    assert entry["fixed"] == {"sound": True}
    assert entry["default_prompt"] == "hello"
    assert "sound" not in entry["input_schema"]["properties"]


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (
            _page_text({"type": "object"}, path="/api/v1/omni/audio/create"),
            "own endpoint",
        ),
        (
            _page_text(
                {
                    "type": "object",
                    "required": ["speakers"],
                    "properties": {
                        "speakers": {"type": "array", "items": {"type": "object"}}
                    },
                }
            ),
            "cannot fill: speakers",
        ),
        (
            _page_text({"type": "object"}).replace('{"resultUrls"', '{"resultObject"'),
            "resultObject",
        ),
    ],
)
def test_what_the_app_cannot_run_is_skipped_with_a_reason(
    sync, monkeypatch, text, reason
):
    with pytest.raises(sync.Skip, match=reason):
        _entry(sync, monkeypatch, text)


def test_the_curated_entries_are_in_the_catalog(sync):
    ids = {e["model_id"] for e in json.loads(sync.OUT.read_text(encoding="utf-8"))}

    assert ids >= sync.CURATED
