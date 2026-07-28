"""
Helpers for ComfyUI **API-format** workflow graphs.

API format is a flat ``{node_id: {"class_type": str, "inputs": dict,
"_meta": {"title": str}}}`` dict -- the shape ``POST /prompt`` accepts.
It is NOT the litegraph UI-export format (top-level ``nodes``/``links``),
which the server rejects. Export with *Workflow > Export (API)*.

Everything here is a pure function over that dict. ``load_graph`` returns a
fresh deep copy each call, so a wrapper can mutate its result freely without
poisoning the next call.
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

# ComfyUI seeds are unsigned 64-bit, but the frontend's own randomizer stays
# well under that; matching it keeps seeds copy-pasteable into the UI.
_SEED_MAX = 2**53 - 1


def load_graph(path: str | Path) -> dict[str, Any]:
    """Load an API-format workflow, raising if handed a UI-format export."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "nodes" in data:
        msg = (
            f"{path} is litegraph UI-export format, not API format. "
            f"Re-export it from ComfyUI with 'Workflow > Export (API)'."
        )
        raise ValueError(msg)

    # ComfyUI writes a node with no class_type and "UNKNOWN" inputs when it
    # exports a node whose custom class is not installed on the exporting
    # machine. The server rejects such a graph, so catch it here with a
    # message that says what to actually do about it.
    broken = [
        nid
        for nid, node in data.items()
        if not isinstance(node, dict) or "class_type" not in node
    ]
    if broken:
        msg = (
            f"{Path(path).name} has {len(broken)} unserialized node(s) "
            f"{broken[:8]}{'...' if len(broken) > 8 else ''} -- exported from a "  # noqa: PLR2004
            f"ComfyUI that was missing those custom nodes. Re-export it from a "
            f"server where every node in the graph is installed."
        )
        raise ValueError(msg)
    return copy.deepcopy(data)


def find_by_class(graph: dict[str, Any], class_type: str) -> list[str]:
    """Node ids whose class_type matches exactly."""
    return [nid for nid, n in graph.items() if n.get("class_type") == class_type]


def find_by_title(graph: dict[str, Any], title: str) -> list[str]:
    """Node ids whose author-set ``_meta.title`` matches exactly.

    Titles are author-supplied and are NOT unique -- 'batch panels' appears
    twice in the carousel graph, 'Positive Prompt' twice in danrisi. Callers
    get a list precisely so an ambiguous match is visible rather than silently
    resolved to whichever node happened to come first.
    """
    return [
        nid for nid, n in graph.items() if (n.get("_meta") or {}).get("title") == title
    ]


def set_input(graph: dict[str, Any], node_id: str, key: str, value: Any) -> None:
    """Set one input on one node. Raises if the node or input is absent.

    A missing input key is an error, not something to create: every input a
    node accepts already exists in the export, so a typo would otherwise be
    silently written into the payload and ignored by the server.
    """
    if node_id not in graph:
        msg = f"node {node_id} not in graph"
        raise KeyError(msg)
    inputs = graph[node_id].setdefault("inputs", {})
    if key not in inputs:
        title = (graph[node_id].get("_meta") or {}).get("title", "")
        msg = (
            f"node {node_id} ({graph[node_id].get('class_type')} {title!r}) "
            f"has no input {key!r}; has {sorted(inputs)}"
        )
        raise KeyError(msg)
    inputs[key] = value


def set_by_title(graph: dict[str, Any], title: str, key: str, value: Any) -> str:
    """Set an input on the single node with this title. Returns the node id."""
    ids = find_by_title(graph, title)
    if not ids:
        msg = f"no node titled {title!r}"
        raise KeyError(msg)
    if len(ids) > 1:
        msg = f"title {title!r} is ambiguous: matches nodes {ids}"
        raise KeyError(msg)
    set_input(graph, ids[0], key, value)
    return ids[0]


def set_first_of_class(
    graph: dict[str, Any], class_type: str, key: str, value: Any
) -> str | None:
    """Set an input on the first node of a class. No-op if the class is absent.

    Used for optional knobs (a batch_size that only some graphs expose), where
    absence is normal rather than a bug.
    """
    ids = find_by_class(graph, class_type)
    if not ids:
        return None
    set_input(graph, ids[0], key, value)
    return ids[0]


def model_inputs(graph: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every (node_id, input_key, filename) referencing a model file.

    Only literal strings -- a list value means the input is fed by a link,
    not a file picker.
    """
    keys = {
        "unet_name",
        "clip_name",
        "vae_name",
        "lora_name",
        "ckpt_name",
        "model_name",
        "sam_model_name",
    }
    found = []
    for nid, node in graph.items():
        for key, value in (node.get("inputs") or {}).items():
            if key in keys and isinstance(value, str):
                found.append((nid, key, value))
    return found


def bypass_node(graph: dict[str, Any], node_id: str, passthrough_key: str) -> None:
    """Splice a node out, reconnecting its consumers to its own upstream.

    The same thing ComfyUI's UI calls "bypass" (node mode 4): a
    LoraLoaderModelOnly whose file is not on the server can be removed from
    the chain by pointing whatever consumed its MODEL at the MODEL it was
    consuming. Used to run a graph whose optional LoRAs are missing.
    """
    if node_id not in graph:
        msg = f"node {node_id} not in graph"
        raise KeyError(msg)
    upstream = (graph[node_id].get("inputs") or {}).get(passthrough_key)
    if not isinstance(upstream, list):
        msg = (
            f"node {node_id} input {passthrough_key!r} is not a link, "
            f"so there is nothing to pass through to"
        )
        raise TypeError(msg)

    for other_id, other in graph.items():
        if other_id == node_id:
            continue
        for key, value in (other.get("inputs") or {}).items():
            if isinstance(value, list) and value and str(value[0]) == str(node_id):
                other["inputs"][key] = upstream
    del graph[node_id]


def bypass_missing_loras(graph: dict[str, Any], available: set[str]) -> list[str]:
    """Bypass every LoraLoaderModelOnly whose file is not available.

    Returns the lora filenames that were dropped, so a caller can say so
    rather than silently producing different images than the graph implies.
    """
    dropped = []
    for node_id in find_by_class(graph, "LoraLoaderModelOnly"):
        name = (graph[node_id].get("inputs") or {}).get("lora_name")
        if isinstance(name, str) and name not in available:
            bypass_node(graph, node_id, "model")
            dropped.append(name)
    return dropped


def random_seed() -> int:
    return random.randint(0, _SEED_MAX)  # noqa: S311


def apply_seed(graph: dict[str, Any], seed: int | None) -> int:
    """Set ``seed`` on every sampler that has one. Returns the seed used.

    ``None`` means "pick a fresh random one", matching the UI's 'randomize'
    control-after-generate. Every sampler gets the same seed so a multi-stage
    graph stays reproducible from one number.
    """
    if seed is None:
        seed = random_seed()
    for node in graph.values():
        inputs = node.get("inputs") or {}
        if "seed" in inputs and not isinstance(inputs["seed"], list):
            inputs["seed"] = seed
        if "noise_seed" in inputs and not isinstance(inputs["noise_seed"], list):
            inputs["noise_seed"] = seed
    return seed
