"""The catalog every page and job reads: every provider's models load, every
schema is valid, and a new provider is picked up with no other change."""

import json

import pytest
from jsonschema import Draft202012Validator

from ofmhelpers.aigenproviders import base
from ofmhelpers.aigenproviders.base import Provider, TaskState, provider_names
from ofmhelpers.aigenproviders.catalog import catalog, get_spec, models_for

# What /generate offered as kie.ai models before /ai: none may go missing.
KIE_MODELS = {
    "nano-banana-pro",
    "bytedance/seedance-2",
    "bytedance/seedance-2-fast",
    "bytedance/seedance-2-mini",
    "bytedance/seedance-2-5",
    "kling-3.0/video",
    "wan/3-0-video",
    "minimax-h3/text-to-video",
    "minimax-h3/image-to-video",
    "minimax-h3/reference-to-video",
    "seedream/4.5-text-to-image",
    "seedream/4.5-edit",
    "seedream/5-lite-text-to-image",
    "seedream/5-lite-image-to-image",
    "seedream/5-pro-text-to-image",
    "seedream/5-pro-image-to-image",
    "gpt-image-2-5-flare-image-to-image",
}


def test_providers_in_select_order():
    assert provider_names() == ["kie", "higgsfield", "fake"]


def test_every_kie_model_is_still_offered():
    assert {s.model_id for s in models_for("kie")} == KIE_MODELS


def test_higgsfield_catalog_has_its_whole_model_list():
    specs = models_for("higgsfield")

    assert len(specs) >= 80
    assert {s.kind for s in specs} == {"image", "video"}
    assert "alibaba/wan-3.0/reference-to-video" in {s.model_id for s in specs}


@pytest.mark.parametrize("spec", list(catalog().values()), ids=lambda s: s.key)
def test_every_schema_is_valid_and_every_field_classified(spec):
    Draft202012Validator.check_schema(spec.input_schema)
    media = {f.name for f in spec.media_fields}
    scalars = {f.name for f in spec.form_fields}
    assert not media & scalars
    if "prompt" in spec.properties:
        assert "prompt" in scalars


def test_get_spec_by_key_and_unknown_key():
    assert get_spec("kie:wan/3-0-video").kind == "video"
    with pytest.raises(KeyError):
        get_spec("kie:nope")


@pytest.fixture
def plugin_provider(tmp_path, monkeypatch):
    """A provider defined outside the package, as a new one would be: a
    `Provider` subclass, its catalog file and its registration -- nothing
    else in the app touched. Dropped again after the test."""
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            [
                {
                    "model_id": "toy/v1",
                    "label": "Toy",
                    "family": "Toy",
                    "kind": "image",
                    "input_schema": {
                        "type": "object",
                        "required": ["prompt"],
                        "properties": {"prompt": {"type": "string"}},
                    },
                }
            ]
        ),
        encoding="utf-8",
    )

    class PluginProvider(Provider):
        name = "plugin"
        label = "Plugin"

        @classmethod
        def catalog_path(cls):
            return path

        @classmethod
        def default_key(cls, admin):
            return "plugin-key" if admin else None

        def upload(self, path):
            return f"https://plugin/{path.name}"

        def submit(self, spec, payload, idempotency_key):
            del spec, payload, idempotency_key
            return "plugin-task"

        def status(self, task_id):
            del task_id
            return TaskState(state="done", urls=["https://plugin/out.png"])

    monkeypatch.setitem(base._registry(), "plugin", PluginProvider)
    catalog.cache_clear()
    yield PluginProvider
    catalog.cache_clear()


def test_a_new_provider_needs_nothing_but_its_class_and_catalog(plugin_provider):
    assert base.provider_class("plugin") is plugin_provider
    assert get_spec("plugin:toy/v1").provider == "plugin"


def test_a_dropped_provider_is_gone_again():
    assert "plugin" not in provider_names()
    assert not models_for("plugin")
