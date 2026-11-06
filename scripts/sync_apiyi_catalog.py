"""Writes integrations/ai_providers/apiyi/catalog.json: every APIYI model the
app offers, hand-made from docs.apiyi.com (APIYI publishes no schema
endpoint; pages read 2026-11-06, see apiyi/CLAUDE notes in
ai_providers/CLAUDE.md). One place for every form: edit here, run
`python scripts/sync_apiyi_catalog.py` (tests image), commit the JSON.

Field names keep the app's usual shape (`image_urls`, `first_frame_url`,
`reference_image_urls`, `aspect_ratio`, `resolution`, `duration`); each
family's `wire_body` turns them into its API's own (`apiyi/families/`).
`x-role`: the media type/role a field's files go as.
"""

import json
from pathlib import Path
from typing import Any

OUT = (
    Path(__file__).resolve().parents[1]
    / "src/ofmhelpers/integrations/ai_providers/apiyi/catalog.json"
)

PROMPT = {"type": "string", "minLength": 1, "title": "Prompt"}
NEGATIVE = {"type": "string", "title": "Negative prompt"}


def media(title: str, kind: str, role: str, many: int | None = None) -> dict[str, Any]:
    field: dict[str, Any] = {"title": title, "x-media": kind, "x-role": role}
    if many:
        field.update({"type": "array", "items": {"type": "string"}, "maxItems": many})
    else:
        field["type"] = "string"
    return field


def choice(title: str, values: list[Any], default: Any) -> dict[str, Any]:
    kind = "integer" if isinstance(values[0], int) else "string"
    return {"type": kind, "enum": values, "default": default, "title": title}


def seconds(low: int, high: int, default: int = 5) -> dict[str, Any]:
    return {
        "type": "integer",
        "minimum": low,
        "maximum": high,
        "default": default,
        "title": "Duration (sec)",
    }


SOUND = {"type": "boolean", "default": True, "title": "Sound"}
NO_FRAME_WITH_REFERENCES = {
    "dependentRequired": {"last_frame_url": ["first_frame_url"]},
    "dependentSchemas": {
        "first_frame_url": {
            "not": {
                "anyOf": [
                    {"required": ["reference_image_urls"]},
                    {"required": ["reference_video_urls"]},
                    {"required": ["reference_audio_urls"]},
                ]
            }
        }
    },
}


def schema(
    properties: dict[str, Any], required: tuple[str, ...] = ("prompt",), **extra: Any
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(required),
        "additionalProperties": False,
        "properties": properties,
        **extra,
    }


def entry(
    model_id: str,
    label: str,
    family: str,
    kind: str,
    input_schema: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "label": label,
        "family": family,
        "kind": kind,
        **extra,
        "input_schema": input_schema,
    }


# --- DashScope: Wan 3.0, Wan 2.7, HappyHorse ---------------------------------

WAN_FIXED = {"prompt_extend": True, "watermark": False}
WAN3 = schema(
    {
        "prompt": PROMPT,
        "negative_prompt": NEGATIVE,
        "first_frame_url": media("First frame", "image", "first_frame"),
        "last_frame_url": media("Last frame", "image", "last_frame"),
        "reference_image_urls": media(
            "Reference images", "image", "reference_image", 10
        ),
        "reference_video_urls": media(
            "Reference videos", "video", "reference_video", 5
        ),
        "reference_audio_urls": media("Reference audio", "audio", "reference_audio", 5),
        "resolution": choice("Resolution", ["480P", "720P", "1080P"], "1080P"),
        "duration": seconds(2, 30),
        "aspect_ratio": choice(
            "Aspect ratio",
            ["adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"],
            "adaptive",
        ),
        "audio": SOUND,
    },
    **NO_FRAME_WITH_REFERENCES,
)
WAN27_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4"]
WAN27_RES = choice("Resolution", ["720P", "1080P"], "720P")
HH_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4", "4:5", "5:4", "9:21", "21:9"]
AUDIO_SETTING = choice("Sound", ["auto", "origin"], "auto")

DASHSCOPE = [
    entry("wan3.0-video", "Wan 3.0", "Wan 3.0", "video", WAN3, fixed=WAN_FIXED),
    entry(
        "wan3.0-video-prime", "Wan 3.0 Prime", "Wan 3.0", "video", WAN3, fixed=WAN_FIXED
    ),
    entry(
        "wan2.7-t2v",
        "Wan 2.7 · Text to video",
        "Wan 2.7",
        "video",
        schema(
            {
                "prompt": PROMPT,
                "negative_prompt": NEGATIVE,
                "resolution": WAN27_RES,
                "aspect_ratio": choice("Aspect ratio", WAN27_RATIOS, "16:9"),
                "duration": seconds(2, 15),
            }
        ),
        fixed=WAN_FIXED,
    ),
    entry(
        "wan2.7-i2v",
        "Wan 2.7 · Image to video",
        "Wan 2.7",
        "video",
        schema(
            {
                "prompt": PROMPT,
                "negative_prompt": NEGATIVE,
                "first_frame_url": media("First frame", "image", "first_frame"),
                "last_frame_url": media("Last frame", "image", "last_frame"),
                "driving_audio_url": media("Lip-sync audio", "audio", "driving_audio"),
                "resolution": WAN27_RES,
                "duration": seconds(2, 15),
            },
            required=("prompt", "first_frame_url"),
            dependentRequired={"last_frame_url": ["first_frame_url"]},
        ),
        fixed=WAN_FIXED,
    ),
    entry(
        "wan2.7-r2v",
        "Wan 2.7 · Reference to video",
        "Wan 2.7",
        "video",
        schema(
            {
                "prompt": PROMPT,
                "negative_prompt": NEGATIVE,
                "reference_image_urls": media(
                    "Reference images", "image", "reference_image", 5
                ),
                "reference_video_urls": media(
                    "Reference videos", "video", "reference_video", 5
                ),
                "resolution": WAN27_RES,
                "aspect_ratio": choice("Aspect ratio", WAN27_RATIOS, "16:9"),
                "duration": seconds(2, 15),
            }
        ),
        fixed=WAN_FIXED,
    ),
    entry(
        "wan2.7-videoedit",
        "Wan 2.7 · Video edit",
        "Wan 2.7",
        "video",
        schema(
            {
                "prompt": PROMPT,
                "video_url": media("Video", "video", "video"),
                "reference_image_urls": media(
                    "Reference images", "image", "reference_image", 4
                ),
                "resolution": WAN27_RES,
                "audio_setting": AUDIO_SETTING,
            },
            required=("prompt", "video_url"),
        ),
        fixed=WAN_FIXED,
    ),
    entry(
        "happyhorse-1.1-t2v",
        "HappyHorse 1.1 · Text to video",
        "HappyHorse",
        "video",
        schema(
            {
                "prompt": PROMPT,
                "resolution": WAN27_RES,
                "aspect_ratio": choice("Aspect ratio", HH_RATIOS, "16:9"),
                "duration": seconds(3, 15),
            }
        ),
        fixed={"watermark": False},
    ),
    entry(
        "happyhorse-1.1-i2v",
        "HappyHorse 1.1 · Image to video",
        "HappyHorse",
        "video",
        schema(
            {
                "prompt": PROMPT,
                "first_frame_url": media("First frame", "image", "first_frame"),
                "resolution": WAN27_RES,
                "duration": seconds(3, 15),
            },
            required=("first_frame_url",),
        ),
        fixed={"watermark": False},
    ),
    entry(
        "happyhorse-1.1-r2v",
        "HappyHorse 1.1 · Reference to video",
        "HappyHorse",
        "video",
        schema(
            {
                "prompt": PROMPT,
                "reference_image_urls": {
                    **media("Reference images", "image", "reference_image", 9),
                    "minItems": 1,
                },
                "resolution": WAN27_RES,
                "aspect_ratio": choice("Aspect ratio", HH_RATIOS, "16:9"),
                "duration": seconds(3, 15),
            },
            required=("prompt", "reference_image_urls"),
        ),
        fixed={"watermark": False},
    ),
    entry(
        "happyhorse-1.0-video-edit",
        "HappyHorse 1.0 · Video edit",
        "HappyHorse",
        "video",
        schema(
            {
                "prompt": PROMPT,
                "video_url": media("Video", "video", "video"),
                "reference_image_urls": media(
                    "Reference images", "image", "reference_image", 5
                ),
                "resolution": WAN27_RES,
                "audio_setting": AUDIO_SETTING,
            },
            required=("prompt", "video_url"),
        ),
        fixed={"watermark": False},
    ),
]


# --- Seedance 2.0 / 2.5 --------------------------------------------------------

SEEDANCE_RATIOS = ["adaptive", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]


def seedance(
    model_id: str,
    label: str,
    resolutions: list[str],
    images: int,
    clips: int,
    longest: int,
) -> dict[str, Any]:
    return entry(
        model_id,
        label,
        "Seedance",
        "video",
        schema(
            {
                "prompt": PROMPT,
                "first_frame_url": media("First frame", "image", "first_frame"),
                "last_frame_url": media("Last frame", "image", "last_frame"),
                "reference_image_urls": media(
                    "Reference images", "image", "reference_image", images
                ),
                "reference_video_urls": media(
                    "Reference videos", "video", "reference_video", clips
                ),
                "reference_audio_urls": media(
                    "Reference audio", "audio", "reference_audio", clips
                ),
                "resolution": choice("Resolution", resolutions, "720p"),
                "aspect_ratio": choice("Aspect ratio", SEEDANCE_RATIOS, "adaptive"),
                "duration": seconds(4, longest),
                "generate_audio": SOUND,
            },
            **NO_FRAME_WITH_REFERENCES,
        ),
        fixed={"watermark": False},
    )


SEEDANCE = [
    seedance(
        "doubao-seedance-2-5-260628",
        "Seedance 2.5",
        ["480p", "720p", "1080p"],
        30,
        10,
        30,
    ),
    seedance(
        "doubao-seedance-2-0-260128",
        "Seedance 2.0",
        ["480p", "720p", "1080p"],
        9,
        3,
        15,
    ),
    seedance(
        "doubao-seedance-2-0-fast-260128",
        "Seedance 2.0 Fast",
        ["480p", "720p"],
        9,
        3,
        15,
    ),
    seedance(
        "doubao-seedance-2-0-mini-260615",
        "Seedance 2.0 Mini",
        ["480p", "720p"],
        9,
        3,
        15,
    ),
]


# --- Veo 3.1 (official) --------------------------------------------------------

VEO = schema(
    {
        "prompt": PROMPT,
        "negative_prompt": NEGATIVE,
        "first_frame_url": media("First frame", "image", "first_frame"),
        "resolution": choice("Resolution", ["720p", "1080p", "4k"], "720p"),
        "aspect_ratio": choice("Aspect ratio", ["16:9", "9:16"], "16:9"),
        "duration": choice("Duration (sec)", [4, 6, 8], 8),
    },
    # 1080p and 4k take only 8 s.
    **{
        "if": {
            "properties": {"resolution": {"enum": ["1080p", "4k"]}},
            "required": ["resolution"],
        },
        "then": {"properties": {"duration": {"const": 8}}},
    },
)
VEO_ENTRIES = [
    entry("veo-3.1-fast-generate-preview", "Veo 3.1 Fast", "Veo 3.1", "video", VEO),
    entry("veo-3.1-generate-preview", "Veo 3.1", "Veo 3.1", "video", VEO),
]


# --- Nano Banana (Gemini) ------------------------------------------------------

BASE_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
WIDE_RATIOS = [*BASE_RATIOS, "1:4", "4:1", "1:8", "8:1"]


def nano(
    model_id: str, label: str, ratios: list[str], sizes: list[str] | None
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "prompt": PROMPT,
        "image_urls": media("Reference images", "image", "reference_image", 14),
        "aspect_ratio": choice("Aspect ratio", ["auto", *ratios], "auto"),
    }
    if sizes:
        properties["resolution"] = choice("Resolution", sizes, "1K")
    return entry(model_id, label, "Nano Banana", "image", schema(properties))


NANO = [
    nano("gemini-3-pro-image", "Nano Banana Pro", BASE_RATIOS, ["1K", "2K", "4K"]),
    nano(
        "gemini-3.1-flash-image",
        "Nano Banana 2",
        WIDE_RATIOS,
        ["512", "1K", "2K", "4K"],
    ),
    nano("gemini-3.1-flash-lite-image", "Nano Banana Lite", WIDE_RATIOS, None),
]


# --- GPT-Image-2 ---------------------------------------------------------------

GPT = [
    entry(
        "gpt-image-2",
        "GPT Image 2",
        "GPT Image",
        "image",
        schema(
            {
                "prompt": PROMPT,
                "image_urls": media("Reference images", "image", "reference_image", 16),
                "size": choice(
                    "Size",
                    [
                        "auto",
                        "1024x1024",
                        "1536x1024",
                        "1024x1536",
                        "2048x2048",
                        "2048x1152",
                        "3840x2160",
                        "2160x3840",
                    ],
                    "auto",
                ),
                "quality": choice("Quality", ["low", "medium", "high"], "medium"),
                "output_format": choice("Format", ["png", "jpeg", "webp"], "png"),
            }
        ),
    ),
    entry(
        "gpt-image-2-all",
        "GPT Image 2 · All",
        "GPT Image",
        "image",
        schema(
            {
                "prompt": PROMPT,
                "image_urls": media("Reference images", "image", "reference_image", 10),
                "size": choice(
                    "Size", ["auto", "1024x1024", "1536x1024", "1024x1536"], "auto"
                ),
            }
        ),
    ),
    entry(
        "gpt-image-2-vip",
        "GPT Image 2 · VIP",
        "GPT Image",
        "image",
        schema(
            {
                "prompt": PROMPT,
                "image_urls": media("Reference images", "image", "reference_image", 10),
                "size": choice(
                    "Size",
                    [
                        "auto",
                        "1280x1280",
                        "1280x720",
                        "720x1280",
                        "2048x2048",
                        "2048x1152",
                        "1152x2048",
                        "2880x2880",
                        "3840x2160",
                        "2160x3840",
                    ],
                    "auto",
                ),
            }
        ),
    ),
]


# --- Seedream ------------------------------------------------------------------


def seedream(
    model_id: str, label: str, sizes: list[str], formats: list[str] | None
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "prompt": PROMPT,
        "image_urls": media("Reference images", "image", "reference_image", 10),
        "size": choice("Size", sizes, "2K" if "2K" in sizes else sizes[0]),
    }
    if formats:
        properties["output_format"] = choice("Format", formats, formats[0])
    return entry(model_id, label, "Seedream", "image", schema(properties))


SEEDREAM = [
    seedream(
        "seedream-5-0-flash-260915",
        "Seedream 5.0 Flash",
        ["1K", "1.5K", "2K"],
        ["jpeg", "png"],
    ),
    seedream("seedream-5-0-260128", "Seedream 5.0", ["2K", "3K"], ["jpeg", "png"]),
    seedream(
        "seedream-5-0-pro-260628", "Seedream 5.0 Pro", ["1K", "2K"], ["jpeg", "png"]
    ),
    seedream("seedream-4-5-251128", "Seedream 4.5", ["2K", "4K"], None),
]


# --- FLUX ----------------------------------------------------------------------

FLUX_SIZES = ["1024x1024", "1536x1024", "1024x1536", "1344x768", "768x1344"]
FLUX_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"]


def flux(
    model_id: str, label: str, images: int, *, edit_only: bool = False
) -> dict[str, Any]:
    refs = media("Reference images", "image", "reference_image", images)
    properties: dict[str, Any] = {"prompt": PROMPT, "image_urls": refs}
    required: tuple[str, ...] = ("prompt",)
    if edit_only:
        refs["minItems"] = 1
        required = ("prompt", "image_urls")
    else:
        properties["size"] = choice("Size", FLUX_SIZES, "1024x1024")
    properties["aspect_ratio"] = choice("Aspect ratio (edits)", FLUX_RATIOS, "1:1")
    properties["output_format"] = choice("Format", ["jpeg", "png"], "jpeg")
    return entry(
        model_id, label, "FLUX", "image", schema(properties, required=required)
    )


FLUX = [
    flux("flux-2-pro", "FLUX.2 Pro", 8),
    flux("flux-2-max", "FLUX.2 Max", 8),
    flux("flux-2-flex", "FLUX.2 Flex", 8),
    flux("flux-2-klein-9b", "FLUX.2 Klein 9B", 4),
    flux("flux-kontext-pro", "FLUX Kontext Pro", 1, edit_only=True),
    flux("flux-kontext-max", "FLUX Kontext Max", 1, edit_only=True),
]


def main() -> None:
    catalog = [*DASHSCOPE, *SEEDANCE, *VEO_ENTRIES, *NANO, *GPT, *SEEDREAM, *FLUX]
    OUT.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(catalog)} models -> {OUT}")


if __name__ == "__main__":
    main()
