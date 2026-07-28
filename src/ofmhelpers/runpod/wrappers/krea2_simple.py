"""
Krea2 Simple -- text to image.

Graph shape: UNETLoader -> KSampler -> VAEDecode -> MoreJPEG -> Image Save.
Node 6 feeds both the positive and negative conditioning (the author wired it
that way; at cfg 1 the negative is ignored anyway), so this exposes a single
prompt and no negative.

Nodes 5/13/32 in the export are disconnected leftovers -- a second
EmptyLatentImage, a ResolutionSelector, and an unused ClownsharKSampler. They
are left alone: ComfyUI only executes what the output node depends on.
"""

from __future__ import annotations

from pathlib import Path

from ofmhelpers.runpod.client import ComfyUIClient
from ofmhelpers.runpod.graph import apply_seed, load_graph, set_input

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "krea2_simple.api.json"

# The export references a differently-built copy of each model than the pod
# actually has on disk; these are the on-pod names.
DEFAULT_UNET = "krea2_turbo_fp8_scaled.safetensors"
DEFAULT_CLIP = "qwen3vl_4b_fp8_scaled.safetensors"
DEFAULT_VAE = "wan_2.1_vae.safetensors"

_PROMPT = "6"
_SAMPLER = "3"
_LATENT = "29"
_UNET = "10"
_CLIP = "11"
_VAE = "12"
_SAVE = "19"

METADATA = {
    "name": "Krea2 Simple",
    "description": "Text-to-image with Krea2 turbo, JPEG-realism post pass.",
    "inputs": [
        {"name": "prompt", "type": "text", "required": True},
        {"name": "width", "type": "int", "default": 1440},
        {"name": "height", "type": "int", "default": 1920},
        {"name": "steps", "type": "int", "default": 8},
        {"name": "cfg", "type": "float", "default": 1.0},
        {"name": "seed", "type": "int", "default": None},
        {"name": "batch", "type": "int", "default": 1},
    ],
}


def krea2_simple(
    prompt: str,
    *,
    width: int = 1440,
    height: int = 1920,
    steps: int = 8,
    cfg: float = 1.0,
    seed: int | None = None,
    batch: int = 1,
    unet: str = DEFAULT_UNET,
    clip: str = DEFAULT_CLIP,
    vae: str = DEFAULT_VAE,
    out_dir: str | Path | None = None,
    client: ComfyUIClient | None = None,
) -> list[Path]:
    """Generate images from a prompt. Returns the downloaded file paths."""
    client = client or ComfyUIClient.from_env()
    graph = load_graph(WORKFLOW)

    set_input(graph, _PROMPT, "text", prompt)
    set_input(graph, _LATENT, "width", width)
    set_input(graph, _LATENT, "height", height)
    set_input(graph, _LATENT, "batch_size", batch)
    set_input(graph, _SAMPLER, "steps", steps)
    set_input(graph, _SAMPLER, "cfg", cfg)
    set_input(graph, _UNET, "unet_name", unet)
    set_input(graph, _CLIP, "clip_name", clip)
    set_input(graph, _VAE, "vae_name", vae)
    # The export hardcodes a Windows output dir from the author's machine,
    # which is not a valid path on the Linux pod.
    set_input(graph, _SAVE, "output_path", "krea2_simple")
    apply_seed(graph, seed)

    return client.run(graph, out_dir=out_dir)


if __name__ == "__main__":
    for path in krea2_simple(
        "a bowl of fruit on a wooden table, soft window light", seed=1234
    ):
        print(path)
