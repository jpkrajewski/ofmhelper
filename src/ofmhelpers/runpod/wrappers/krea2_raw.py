"""
Krea2 Raw -- text to image, photographic/amateur look.

Graph shape: UNETLoader -> 3x LoraLoaderModelOnly -> KSampler -> VAEDecode
-> PreviewImage. The negative is a ConditioningZeroOut of the positive, so
there is no separate negative prompt to expose.

None of the three LoRAs this graph references are on the pod. They are
bypassed by default (`use_loras=False`) so the wrapper runs; the result is
the base model's look rather than the authored one. Set `use_loras=True`
once the files are in `models/loras/` to get the intended output.
"""

from __future__ import annotations

from pathlib import Path

from ofmhelpers.log import get_logger
from ofmhelpers.runpod.client import ComfyUIClient
from ofmhelpers.runpod.graph import (
    apply_seed,
    bypass_missing_loras,
    load_graph,
    set_input,
)

logger = get_logger(__name__)

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "krea2_raw.api.json"

# The export names a fp8 build of the raw model; the pod carries the bf16 one.
DEFAULT_UNET = "krea2_raw_bf16.safetensors"
DEFAULT_CLIP = "qwen3vl_4b_fp8_scaled.safetensors"
DEFAULT_VAE = "wan_2.1_vae.safetensors"

_PROMPT = "9"
_SAMPLER = "8"
_LATENT = "5"
_UNET = "7"
_CLIP = "2"
_VAE = "3"

METADATA = {
    "name": "Krea2 Raw",
    "description": "Text-to-image with the Krea2 raw model, photographic look.",
    "inputs": [
        {"name": "prompt", "type": "text", "required": True},
        {"name": "width", "type": "int", "default": 768},
        {"name": "height", "type": "int", "default": 1216},
        {"name": "steps", "type": "int", "default": 14},
        {"name": "cfg", "type": "float", "default": 1.0},
        {"name": "seed", "type": "int", "default": None},
        {"name": "batch", "type": "int", "default": 1},
        {"name": "use_loras", "type": "bool", "default": False},
    ],
}


def krea2_raw(
    prompt: str,
    *,
    width: int = 768,
    height: int = 1216,
    steps: int = 14,
    cfg: float = 1.0,
    seed: int | None = None,
    batch: int = 1,
    use_loras: bool = False,
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
    apply_seed(graph, seed)

    if not use_loras:
        available = set(
            client.object_info("LoraLoaderModelOnly")["LoraLoaderModelOnly"]["input"][
                "required"
            ]["lora_name"][0]
        )
        dropped = bypass_missing_loras(graph, available)
        if dropped:
            logger.warning("bypassed %d missing lora(s): %s", len(dropped), dropped)

    return client.run(graph, out_dir=out_dir)


if __name__ == "__main__":
    for path in krea2_raw("a ceramic mug on a windowsill, overcast daylight", seed=7):
        print(path)
