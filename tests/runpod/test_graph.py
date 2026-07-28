"""Covers runpod/graph.py -- pure helpers over an API-format graph."""

import json

import pytest

from ofmhelpers.runpod.graph import (
    apply_seed,
    bypass_missing_loras,
    bypass_node,
    find_by_class,
    find_by_title,
    load_graph,
    model_inputs,
    set_by_title,
    set_input,
)


def _graph():
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "a.safetensors"}},
        "2": {
            "class_type": "LoraLoaderModelOnly",
            "inputs": {"lora_name": "missing.safetensors", "model": ["1", 0]},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {"model": ["2", 0], "seed": 1},
            "_meta": {"title": "sampler"},
        },
    }


def test_load_graph_rejects_ui_format(tmp_path):
    path = tmp_path / "ui.json"
    path.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="UI-export format"):
        load_graph(path)


def test_load_graph_rejects_unserialized_nodes(tmp_path):
    """A node exported without class_type means the exporting ComfyUI lacked
    that custom node -- the server would reject the graph with a far less
    obvious error."""
    path = tmp_path / "api.json"
    path.write_text(
        json.dumps({"1": {"inputs": {"UNKNOWN": True}, "_meta": {}}}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="unserialized node"):
        load_graph(path)


def test_load_graph_returns_independent_copies(tmp_path):
    path = tmp_path / "api.json"
    path.write_text(json.dumps(_graph()), encoding="utf-8")

    first = load_graph(path)
    first["1"]["inputs"]["unet_name"] = "mutated"

    assert load_graph(path)["1"]["inputs"]["unet_name"] == "a.safetensors"


def test_find_helpers():
    graph = _graph()

    assert find_by_class(graph, "KSampler") == ["3"]
    assert find_by_title(graph, "sampler") == ["3"]
    assert find_by_title(graph, "nope") == []


def test_set_input_rejects_unknown_key():
    graph = _graph()

    with pytest.raises(KeyError, match="has no input"):
        set_input(graph, "3", "nonexistent", 1)


def test_set_by_title_rejects_ambiguous_title():
    graph = _graph()
    graph["4"] = {"class_type": "KSampler", "inputs": {}, "_meta": {"title": "sampler"}}

    with pytest.raises(KeyError, match="ambiguous"):
        set_by_title(graph, "sampler", "seed", 5)


def test_bypass_node_reconnects_consumers_to_upstream():
    graph = _graph()

    bypass_node(graph, "2", "model")

    assert "2" not in graph
    assert graph["3"]["inputs"]["model"] == ["1", 0]


def test_bypass_missing_loras_only_drops_absent_files():
    graph = _graph()

    dropped = bypass_missing_loras(graph, available={"present.safetensors"})

    assert dropped == ["missing.safetensors"]
    assert graph["3"]["inputs"]["model"] == ["1", 0]

    unchanged = _graph()
    assert bypass_missing_loras(unchanged, available={"missing.safetensors"}) == []
    assert "2" in unchanged


def test_apply_seed_sets_every_sampler_and_returns_it():
    graph = _graph()
    graph["4"] = {"class_type": "KSampler", "inputs": {"seed": 2}}

    used = apply_seed(graph, 999)

    assert used == 999
    assert graph["3"]["inputs"]["seed"] == 999
    assert graph["4"]["inputs"]["seed"] == 999


def test_apply_seed_none_picks_a_random_one():
    graph = _graph()

    used = apply_seed(graph, None)

    assert graph["3"]["inputs"]["seed"] == used


def test_model_inputs_skips_linked_values():
    graph = _graph()
    graph["1"]["inputs"]["unet_name"] = ["9", 0]

    assert ("1", "unet_name", "a.safetensors") not in model_inputs(graph)
    assert ("2", "lora_name", "missing.safetensors") in model_inputs(graph)
