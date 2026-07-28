"""Covers the workflow wrappers: the right nodes get mutated, no HTTP."""

from pathlib import Path

import pytest

from ofmhelpers.runpod import deps
from ofmhelpers.runpod.wrappers import krea2_raw, krea2_simple


class FakeClient:
    """Captures the graph a wrapper submits instead of running it."""

    def __init__(self, loras=()):
        self.graph = None
        self._loras = list(loras)

    def object_info(self, node_class=None):  # noqa: ARG002
        return {
            "LoraLoaderModelOnly": {"input": {"required": {"lora_name": [self._loras]}}}
        }

    def upload_image(self, path, **_):
        return Path(path).name

    def run(self, graph, **_):
        self.graph = graph
        return [Path("fake.png")]


def test_krea2_simple_sets_prompt_size_and_models():
    fake = FakeClient()

    krea2_simple.krea2_simple(
        "a test prompt", width=512, height=640, steps=3, seed=99, client=fake
    )

    graph = fake.graph
    assert graph["6"]["inputs"]["text"] == "a test prompt"
    assert graph["29"]["inputs"]["width"] == 512
    assert graph["29"]["inputs"]["height"] == 640
    assert graph["3"]["inputs"]["steps"] == 3
    assert graph["3"]["inputs"]["seed"] == 99
    assert graph["10"]["inputs"]["unet_name"] == krea2_simple.DEFAULT_UNET
    assert graph["12"]["inputs"]["vae_name"] == krea2_simple.DEFAULT_VAE


def test_krea2_simple_overrides_the_authors_windows_output_path():
    fake = FakeClient()

    krea2_simple.krea2_simple("x", client=fake)

    assert "D:\\" not in fake.graph["19"]["inputs"]["output_path"]


def test_krea2_raw_bypasses_loras_the_server_does_not_have():
    fake = FakeClient(loras=[])

    krea2_raw.krea2_raw("x", client=fake)

    assert not [
        n for n in fake.graph.values() if n["class_type"] == "LoraLoaderModelOnly"
    ]
    # KSampler must now take its model straight from the UNETLoader.
    assert fake.graph["8"]["inputs"]["model"] == ["7", 0]


def test_krea2_raw_keeps_loras_when_they_are_present():
    fake = FakeClient(
        loras=[
            "RawGirlV2_epoch_10.safetensors",
            "krea2_turbo_lora_rank_64_bf16.safetensors",
            "realism_engine_krea2_v3.1.safetensors",
        ]
    )

    krea2_raw.krea2_raw("x", client=fake)

    assert fake.graph["8"]["inputs"]["model"] == ["17", 0]


@pytest.mark.parametrize("name", ["krea2_danrisi", "krea2_krast"])
def test_incomplete_exports_fail_with_an_actionable_message(name):
    """These two workflows were exported from a ComfyUI missing some of their
    custom nodes, so they carry nodes with no class_type."""
    from importlib import import_module

    module = import_module(f"ofmhelpers.runpod.wrappers.{name}")

    with pytest.raises(ValueError, match="unserialized node"):
        module.load_graph(module.WORKFLOW)


def test_missing_models_ignores_loaders_the_server_lacks_entirely():
    """An absent loader is a missing *node*, already reported separately --
    don't also report every model it would have listed."""
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "a.safetensors"}}
    }

    assert deps.missing_models(graph, object_info={}) == []


def test_missing_models_reports_a_file_absent_from_a_present_loader():
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "a.safetensors"}}
    }
    object_info = {
        "UNETLoader": {"input": {"required": {"unet_name": [["b.safetensors"]]}}}
    }

    assert deps.missing_models(graph, object_info) == [
        ("1", "unet_name", "a.safetensors")
    ]


def test_resolve_repos_maps_classes_to_their_source_repo():
    node_map = {"https://github.com/x/pack": ["CoolNode", "OtherNode"]}

    assert deps.resolve_repos(["CoolNode", "Absent"], node_map) == {
        "CoolNode": "https://github.com/x/pack",
        "Absent": None,
    }
