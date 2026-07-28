"""
Krea2 danrisi -- text to image, film-emulation LoRA stack.

NOTE: the committed export is incomplete. Nodes 28 and 29 (the VAE loader and
decode feeding SaveImage) came out with no `class_type` and `"UNKNOWN"`
inputs, meaning the ComfyUI that produced it did not have those nodes
installed. `load_graph` rejects it with that explanation. Re-export this
workflow from the pod -- where every node is installed -- and it will work
without changing this module.

Resolution comes from a FluxResolutionNode rather than literal width/height,
so `megapixels` is the size knob here.
"""

from __future__ import annotations

from pathlib import Path

from ofmhelpers.log import get_logger
from ofmhelpers.runpod.client import ComfyUIClient
from ofmhelpers.runpod.graph import (
    apply_seed,
    load_graph,
    set_input,
)

logger = get_logger(__name__)

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "krea2_danrisi.api.json"

# Export names an int8 build and an abliterated Qwen3-VL; the pod has neither.
DEFAULT_UNET = "krea2_raw_bf16.safetensors"
DEFAULT_CLIP = "qwen3vl_4b_fp8_scaled.safetensors"

_POSITIVE = "6"
_NEGATIVE = "27"
_SAMPLER = "25"
_RESOLUTION = "16"
_UNET = "1"
_CLIP = "13"

METADATA = {
    "name": "Krea2 danrisi",
    "description": "Text-to-image with the danrisi film-look LoRA stack.",
    "inputs": [
        {"name": "prompt", "type": "text", "required": True},
        {"name": "negative", "type": "text", "default": None},
        {"name": "megapixels", "type": "float", "default": 2.0},
        {"name": "steps", "type": "int", "default": 50},
        {"name": "cfg", "type": "float", "default": 4.0},
        {"name": "seed", "type": "int", "default": None},
    ],
}


def krea2_danrisi(
    prompt: str,
    *,
    negative: str | None = None,
    megapixels: float = 2.0,
    steps: int = 50,
    cfg: float = 4.0,
    seed: int | None = None,
    unet: str = DEFAULT_UNET,
    clip: str = DEFAULT_CLIP,
    out_dir: str | Path | None = None,
    client: ComfyUIClient | None = None,
) -> list[Path]:
    """Generate images from a prompt. Returns the downloaded file paths."""
    client = client or ComfyUIClient.from_env()
    graph = load_graph(WORKFLOW)

    set_input(graph, _POSITIVE, "text", prompt)
    if negative is not None:
        set_input(graph, _NEGATIVE, "text", negative)
    set_input(graph, _RESOLUTION, "resolution", str(megapixels))
    set_input(graph, _SAMPLER, "steps", steps)
    set_input(graph, _SAMPLER, "cfg", cfg)
    set_input(graph, _UNET, "unet_name", unet)
    set_input(graph, _CLIP, "clip_name", clip)
    # The export pins the text encoder to CPU, which is minutes per run.
    set_input(graph, _CLIP, "device", "default")
    apply_seed(graph, seed)

    return client.run(graph, out_dir=out_dir)


if __name__ == "__main__":
    for path in krea2_danrisi("a wooden bench in a park, autumn afternoon", seed=11):
        print(path)
