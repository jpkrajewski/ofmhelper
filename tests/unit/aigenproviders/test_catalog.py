import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ofmhelpers.integrations.ai_providers import base
from ofmhelpers.integrations.ai_providers.base import (
    Provider,
    TaskState,
    provider_names,
)
from ofmhelpers.integrations.ai_providers.catalog import catalog, get_spec, models_for
from ofmhelpers.integrations.ai_providers.kie.provider import KieProvider

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
    assert provider_names() == [
        "kie",
        "higgsfield",
        "runninghub",
        "elyum",
        "apiyi",
        "fake",
    ]


def test_every_kie_model_is_still_offered():
    assert {s.model_id for s in models_for("kie")} >= KIE_MODELS


def test_kie_catalog_has_its_whole_model_list():
    raw = json.loads(Path(KieProvider.catalog_path()).read_text(encoding="utf-8"))
    ids = [e["model_id"] for e in raw]

    assert len(ids) == len(set(ids))
    assert len(ids) >= 150
    assert {s.kind for s in models_for("kie")} == {"image", "video"}
    assert {"veo-3-1", "runway", "topaz/video-upscale", "google/imagen4"} <= set(ids)


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
    assert {"prompt"} & set(spec.properties) <= scalars


@pytest.mark.parametrize("spec", list(catalog().values()), ids=lambda s: s.key)
def test_every_required_field_is_one_the_form_fills(spec):
    assert spec.required <= {f.name for f in spec.form_fields} | {
        f.name for f in spec.media_fields
    }


@pytest.mark.parametrize("spec", list(catalog().values()), ids=lambda s: s.key)
def test_no_field_collides_with_the_run_forms_own(spec):
    assert not {"provider", "model", "api_key"} & set(spec.properties)


@pytest.mark.parametrize("spec", list(catalog().values()), ids=lambda s: s.key)
def test_no_default_is_a_url(spec):
    urls = {
        name: prop["default"]
        for name, prop in spec.input_schema.get("properties", {}).items()
        if isinstance(prop.get("default"), str)
        and prop["default"].startswith(("http://", "https://"))
    }
    assert not urls


def test_get_spec_by_key_and_unknown_key():
    assert get_spec("kie:wan/3-0-video").kind == "video"
    with pytest.raises(KeyError):
        get_spec("kie:nope")


@pytest.fixture
def plugin_provider(tmp_path, monkeypatch):
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
        def default_key(cls):
            return "plugin-key"

        def upload(self, path, max_bytes=None):  # noqa: ARG002
            return f"https://plugin/{path.name}"

        def submit(self, spec, payload, idempotency_key):
            del spec, payload, idempotency_key
            return "plugin-task"

        def status(self, task_id):
            del task_id
            return TaskState(state="done", urls=["https://plugin/out.png"])

    monkeypatch.setitem(base._registered().classes, "plugin", PluginProvider)
    catalog.cache_clear()
    yield PluginProvider
    catalog.cache_clear()


def test_a_new_provider_needs_nothing_but_its_class_and_catalog(plugin_provider):
    assert base.provider_class("plugin") is plugin_provider
    assert get_spec("plugin:toy/v1").provider == "plugin"


def test_a_dropped_provider_is_gone_again():
    assert "plugin" not in provider_names()
    assert not models_for("plugin")
