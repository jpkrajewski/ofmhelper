"""Every kie.ai model id and per-model enum value, in one place.

`KieAIClient` and the generation routers both import from here, so a model id
or an allowed value is spelled exactly once. Values are kie.ai's own spelling --
note the case differences between models (Wan "1080P" vs Seedance "1080p").
"""

from enum import StrEnum


class KieModel(StrEnum):
    NANO_BANANA_PRO = "nano-banana-pro"
    SEEDANCE_2 = "bytedance/seedance-2"
    SEEDANCE_2_FAST = "bytedance/seedance-2-fast"
    SEEDANCE_2_MINI = "bytedance/seedance-2-mini"
    SEEDANCE_2_5 = "bytedance/seedance-2-5"
    KLING_3 = "kling-3.0/video"
    WAN_3 = "wan/3-0-video"
    MINIMAX_H3_TEXT = "minimax-h3/text-to-video"
    MINIMAX_H3_IMAGE = "minimax-h3/image-to-video"
    MINIMAX_H3_REFERENCE = "minimax-h3/reference-to-video"
    SEEDREAM_4_5_TEXT = "seedream/4.5-text-to-image"
    SEEDREAM_4_5_EDIT = "seedream/4.5-edit"
    SEEDREAM_5_LITE_TEXT = "seedream/5-lite-text-to-image"
    SEEDREAM_5_LITE_IMAGE = "seedream/5-lite-image-to-image"
    SEEDREAM_5_PRO_TEXT = "seedream/5-pro-text-to-image"
    SEEDREAM_5_PRO_IMAGE = "seedream/5-pro-image-to-image"
    GPT_IMAGE_2_5_FLARE_IMAGE = "gpt-image-2-5-flare-image-to-image"


# Models whose result is a clip -- resume_pending names a recovered download
# by this, everything else is an image.
VIDEO_MODELS = frozenset(
    {
        KieModel.SEEDANCE_2,
        KieModel.SEEDANCE_2_FAST,
        KieModel.SEEDANCE_2_MINI,
        KieModel.SEEDANCE_2_5,
        KieModel.KLING_3,
        KieModel.WAN_3,
        KieModel.MINIMAX_H3_TEXT,
        KieModel.MINIMAX_H3_IMAGE,
        KieModel.MINIMAX_H3_REFERENCE,
    }
)

VIDEO_EXT = "mp4"
IMAGE_EXT = "png"
ADAPTIVE_ASPECT_RATIO = "adaptive"


class Seedance2Model(StrEnum):
    """The Seedance 2.0 tiers -- one input shape, three price/speed points."""

    STANDARD = KieModel.SEEDANCE_2.value
    FAST = KieModel.SEEDANCE_2_FAST.value
    MINI = KieModel.SEEDANCE_2_MINI.value


class SeedanceResolution(StrEnum):
    P480 = "480p"
    P720 = "720p"
    P1080 = "1080p"


class Kling3Mode(StrEnum):
    STD = "std"
    PRO = "pro"
    K4 = "4K"


class Wan3Resolution(StrEnum):
    P480 = "480P"
    P720 = "720P"
    P1080 = "1080P"


class MinimaxH3Resolution(StrEnum):
    P768 = "768P"
    K2 = "2K"


class ImageResolution(StrEnum):
    """Nano Banana Pro and GPT Image 2.5 Flare tiers."""

    K1 = "1K"
    K2 = "2K"
    K4 = "4K"


class ImageBackground(StrEnum):
    """GPT Image 2.5 Flare."""

    AUTO = "auto"
    OPAQUE = "opaque"
    TRANSPARENT = "transparent"


class Seedream45Quality(StrEnum):
    BASIC = "basic"  # 2K
    HIGH = "high"  # 4K


class Seedream5Variant(StrEnum):
    LITE = "lite"
    PRO = "pro"


class Seedream5Quality(StrEnum):
    BASIC = "basic"  # Lite 2K, Pro 1K
    HIGH = "high"  # Lite 3K, Pro 2K
    ULTRA = "ultra"  # Lite 4K; Pro rejects it


class SeedreamOutputFormat(StrEnum):
    PNG = "png"
    JPEG = "jpeg"


# (text-to-image, image-to-image) per Seedream 5.0 variant.
SEEDREAM5_MODELS = {
    Seedream5Variant.LITE: (
        KieModel.SEEDREAM_5_LITE_TEXT,
        KieModel.SEEDREAM_5_LITE_IMAGE,
    ),
    Seedream5Variant.PRO: (
        KieModel.SEEDREAM_5_PRO_TEXT,
        KieModel.SEEDREAM_5_PRO_IMAGE,
    ),
}
