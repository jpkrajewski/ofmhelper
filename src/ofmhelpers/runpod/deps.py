"""
What a workflow needs that the server does not have.

Reports only -- it never installs. Auto-installing custom nodes or pulling
multi-GB checkpoints into a live pod is not worth the blast radius; printing
the `git clone` lines for a human is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ofmhelpers.log import get_logger
from ofmhelpers.runpod.client import ComfyUIClient
from ofmhelpers.runpod.graph import model_inputs

logger = get_logger(__name__)

# Loader input -> the /object_info node+field whose enum lists what is on disk.
_MODEL_POOLS = {
    "unet_name": ("UNETLoader", "unet_name"),
    "clip_name": ("CLIPLoader", "clip_name"),
    "vae_name": ("VAELoader", "vae_name"),
    "lora_name": ("LoraLoaderModelOnly", "lora_name"),
    "ckpt_name": ("CheckpointLoaderSimple", "ckpt_name"),
}


def missing_nodes(graph: dict[str, Any], object_info: dict[str, Any]) -> list[str]:
    """Class types used by the graph that the server does not implement."""
    used = {n.get("class_type") for n in graph.values() if n.get("class_type")}
    return sorted(c for c in used if c not in object_info)


def _pool(object_info: dict[str, Any], node_class: str, field: str) -> set[str]:
    try:
        options = object_info[node_class]["input"]["required"][field][0]
    except (KeyError, IndexError, TypeError):
        return set()
    return set(options) if isinstance(options, list) else set()


def missing_models(
    graph: dict[str, Any], object_info: dict[str, Any]
) -> list[tuple[str, str, str]]:
    """(node_id, input_key, filename) for every model file not on the server."""
    missing = []
    for node_id, key, filename in model_inputs(graph):
        if key not in _MODEL_POOLS:
            continue
        node_class, field = _MODEL_POOLS[key]
        pool = _pool(object_info, node_class, field)
        # An empty pool means that loader is not installed at all -- that is a
        # missing *node*, already reported by missing_nodes(); don't also call
        # every one of its models missing.
        if pool and filename not in pool:
            missing.append((node_id, key, filename))
    return missing


def load_node_map(path: str | Path) -> dict[str, list[str]]:
    """Read ComfyUI-Manager's extension-node-map.json -> {repo_url: [classes]}.

    Lives on the pod at
    custom_nodes/ComfyUI-Manager/extension-node-map.json.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        url: value[0]
        for url, value in raw.items()
        if isinstance(value, list) and value and isinstance(value[0], list)
    }


def resolve_repos(
    class_names: list[str], node_map: dict[str, list[str]]
) -> dict[str, str | None]:
    """Map each missing class to the repo that provides it (None if unknown)."""
    owner: dict[str, str | None] = dict.fromkeys(class_names)
    for url, classes in node_map.items():
        wanted = set(classes) & set(class_names)
        for name in wanted:
            if owner[name] is None:
                owner[name] = url
    return owner


def report(
    graph: dict[str, Any],
    *,
    client: ComfyUIClient | None = None,
    node_map_path: str | Path | None = None,
) -> dict[str, Any]:
    """Everything missing for one graph, as plain data."""
    client = client or ComfyUIClient.from_env()
    object_info = client.object_info()

    nodes = missing_nodes(graph, object_info)
    models = missing_models(graph, object_info)
    repos: dict[str, str | None] = {}
    if nodes and node_map_path:
        repos = resolve_repos(nodes, load_node_map(node_map_path))

    return {
        "missing_nodes": nodes,
        "missing_models": models,
        "node_repos": repos,
        "runnable": not nodes and not models,
    }


def format_report(result: dict[str, Any]) -> str:
    """Human-readable version of report(), for a __main__ block."""
    if result["runnable"]:
        return "runnable: every node and model is present"

    lines = []
    if result["missing_nodes"]:
        lines.append(f"missing custom nodes ({len(result['missing_nodes'])}):")
        for name in result["missing_nodes"]:
            repo = (result.get("node_repos") or {}).get(name)
            lines.append(f"  {name}" + (f"  ->  git clone {repo}" if repo else ""))
    if result["missing_models"]:
        lines.append(f"missing models ({len(result['missing_models'])}):")
        for node_id, key, filename in result["missing_models"]:
            lines.append(f"  node {node_id}  {key} = {filename}")
    return "\n".join(lines)
