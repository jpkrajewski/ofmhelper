"""
Krea2 KRAST -- text to image, two-stage with a hi-res fix.

NOTE: the committed export is incomplete. Twelve nodes came out with no
`class_type` and `"UNKNOWN"` inputs -- including the diffusion model, CLIP and
VAE loaders themselves (they hold `KRAST.safetensors`, the abliterated
Qwen3-VL, and `krea2RealVae_v10.safetensors`), plus a rgthree LoRA stack and
several reroutes. The ComfyUI that produced this export did not have those
nodes installed, so the file cannot be submitted and the model names it
references are not visible to a dependency check either.

`load_graph` rejects it with that explanation. Re-export from the pod, where
every node is installed. The node ids below are stable across a re-export
(they are the subgraph-flattened ids, e.g. `9313:25`), so this module should
work as-is once the file is replaced.
"""

from __future__ import annotations

from pathlib import Path

from ofmhelpers.runpod.client import ComfyUIClient
from ofmhelpers.runpod.graph import apply_seed, load_graph, set_input

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "krea2_krast.api.json"

_POSITIVE = "6"
_STAGE1 = "9313:25"
_STAGE2 = "9257:9171"

METADATA = {
    "name": "Krea2 KRAST",
    "description": "Two-stage text-to-image with a latent-upscale hi-res fix.",
    "inputs": [
        {"name": "prompt", "type": "text", "required": True},
        {"name": "steps", "type": "int", "default": 8},
        {"name": "hires_steps", "type": "int", "default": 10},
        {"name": "hires_denoise", "type": "float", "default": 0.5},
        {"name": "seed", "type": "int", "default": None},
    ],
}


def krea2_krast(
    prompt: str,
    *,
    steps: int = 8,
    hires_steps: int = 10,
    hires_denoise: float = 0.5,
    seed: int | None = None,
    out_dir: str | Path | None = None,
    client: ComfyUIClient | None = None,
) -> list[Path]:
    """Generate images from a prompt. Returns the downloaded file paths."""
    client = client or ComfyUIClient.from_env()
    graph = load_graph(WORKFLOW)

    set_input(graph, _POSITIVE, "text", prompt)
    set_input(graph, _STAGE1, "steps", steps)
    set_input(graph, _STAGE2, "steps", hires_steps)
    set_input(graph, _STAGE2, "denoise", hires_denoise)
    apply_seed(graph, seed)

    return client.run(graph, out_dir=out_dir)


if __name__ == "__main__":
    for path in krea2_krast("a stone bridge over a river, morning mist", seed=5):
        print(path)
