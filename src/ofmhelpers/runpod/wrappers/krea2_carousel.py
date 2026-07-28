"""
Krea2 Carousel -- image to image, one source photo into a 4-panel set.

Panel 1 is the source; panels 2-4 are edits of it, each driven by one
instruction line. In the exported graph those lines come from an AILab_QwenVL
node that writes them from a template. Passing ``panel_instructions``
replaces that with literal strings, which also means the QwenVL branch never
executes -- nothing downstream depends on it -- so the Qwen3-VL weights are
not needed at all. Pass ``panel_instructions=None`` to use the graph's own
LLM director instead.

Two of this graph's LoRAs (a character LoRA, a POV LoRA) are not on the pod
and are bypassed automatically; the identity-edit LoRA is essential to the
edit itself and is substituted to the version the pod carries.
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

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "krea2_carousel.api.json"

# Export references v1_1 of the identity LoRA and bf16/fp32 model builds; the
# pod has v1_2 and the fp8/2.1 builds.
DEFAULT_IDENTITY_LORA = "krea2_identity_edit_v1_2.safetensors"
DEFAULT_UNET = "krea2_turbo_fp8_scaled.safetensors"
DEFAULT_CLIP = "qwen3vl_4b_fp8_scaled.safetensors"
DEFAULT_VAE = "wan_2.1_vae.safetensors"

_LOAD_IMAGE = "1030"
_SCALE = "1031"
_IDENTITY_LORA = "1033"
_UNET = "55"
_CLIP = "56"
_VAE = "57"
_LATENT = "1039"
_DETAILER_PROMPT = "1063"
# One ImpactStringSelector per panel; each picks its own line from the shared
# instruction text.
_PANEL_SELECTORS = ("1052", "2002", "2003")
_PANEL_COUNT = len(_PANEL_SELECTORS)

METADATA = {
    "name": "Krea2 Carousel",
    "description": "Turn one source image into a 4-panel carousel of edits.",
    "inputs": [
        {"name": "image_path", "type": "image", "required": True},
        {"name": "panel_instructions", "type": "text[]", "default": None},
        {"name": "width", "type": "int", "default": 1024},
        {"name": "height", "type": "int", "default": 1280},
        {"name": "steps", "type": "int", "default": 8},
        {"name": "seed", "type": "int", "default": None},
    ],
}


def krea2_carousel(
    image_path: str | Path,
    panel_instructions: list[str] | None = None,
    *,
    width: int = 1024,
    height: int = 1280,
    steps: int = 8,
    seed: int | None = None,
    detailer_prompt: str | None = None,
    identity_lora: str = DEFAULT_IDENTITY_LORA,
    unet: str = DEFAULT_UNET,
    clip: str = DEFAULT_CLIP,
    vae: str = DEFAULT_VAE,
    out_dir: str | Path | None = None,
    client: ComfyUIClient | None = None,
) -> list[Path]:
    """Build a carousel from one source image. Returns downloaded file paths."""
    if panel_instructions is not None and len(panel_instructions) != _PANEL_COUNT:
        msg = (
            f"panel_instructions needs exactly {_PANEL_COUNT} entries "
            f"(panels 2-4), got {len(panel_instructions)}"
        )
        raise ValueError(msg)

    client = client or ComfyUIClient.from_env()
    graph = load_graph(WORKFLOW)

    uploaded = client.upload_image(image_path)
    set_input(graph, _LOAD_IMAGE, "image", uploaded)

    set_input(graph, _SCALE, "width", width)
    set_input(graph, _SCALE, "height", height)
    set_input(graph, _LATENT, "width", width)
    set_input(graph, _LATENT, "height", height)
    set_input(graph, _IDENTITY_LORA, "lora_name", identity_lora)
    set_input(graph, _UNET, "unet_name", unet)
    set_input(graph, _CLIP, "clip_name", clip)
    set_input(graph, _VAE, "vae_name", vae)
    if detailer_prompt is not None:
        set_input(graph, _DETAILER_PROMPT, "text", detailer_prompt)

    for node_id in graph:
        if graph[node_id].get("class_type") == "KSampler":
            set_input(graph, node_id, "steps", steps)
    apply_seed(graph, seed)

    if panel_instructions is not None:
        # Replace the link to the LLM node with the literal lines it would
        # have produced; each selector still picks its own index.
        joined = "\n".join(panel_instructions)
        for node_id in _PANEL_SELECTORS:
            set_input(graph, node_id, "strings", joined)

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
    IMAGE = (
        r"C:\Users\jakub\Downloads\hf_12312312312.JPG"  # <-- put your source image here
    )

    INSTRUCTIONS = [
        (
            "Make a new shot of the woman, standing with her weight on her back "
            "foot, hands relaxed at her sides, looking off to the left, camera "
            "pulled slightly wider. same outfit, same room, same light, same person."
        ),
        (
            "Make a new shot of the woman, seated and turned toward the camera, "
            "one hand resting on her knee, calm neutral expression, camera at eye "
            "level. same outfit, same room, same light, same person."
        ),
        (
            "Make a new shot of the woman, walking forward, arms swinging "
            "naturally, head turned away from the lens, camera following from a "
            "three-quarter angle. same outfit, same room, same light, same person."
        ),
    ]

    for path in krea2_carousel(IMAGE, INSTRUCTIONS, seed=42):
        print(path)
