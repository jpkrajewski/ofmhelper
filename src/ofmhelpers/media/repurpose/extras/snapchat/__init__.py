"""The Snapchat caption bar: a translucent black strip across the frame's
middle with white centred text (the classic snap-caption look).

Measured off a reference snap video (2026-10-29, 2160x3840) -- the fractions
below are of the frame's height, so any output size gets the same look:

- bar: full width, 5.0% of the frame's height, centred at 49.4% of it,
  black at 61% opacity;
- text: white, centred in the bar, bounding box 2.29% of the frame's height;
- font: regular-weight Arial/Helvetica class (Liberation Sans is metrically
  identical to Arial and ships with Linux images; DejaVu is the last resort
  and is noticeably wider, so it is only a fallback).

`render` writes a full-frame RGBA PNG; the video transformer overlays it as
the last filter-graph segment (over vignette, grain, everything).
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ofmhelpers.config import settings

# Fractions of the frame height, measured from the reference.
BAR_H = 0.0500
BAR_CENTER = 0.4938
BAR_ALPHA = 0.61
TEXT_BBOX_H = 0.0229
# Arial / Liberation Sans: rendered bbox height = 0.94 * point size.
_FONT_BBOX_RATIO = 0.94

_FONT_ENV = "OFM_SNAPCHAT_FONT"
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def find_font() -> str:
    """The caption font: $OFM_SNAPCHAT_FONT, else the first installed candidate."""
    chosen = settings.repurpose.snapchat_font
    if chosen and Path(chosen).is_file():
        return chosen
    for candidate in _FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    msg = (
        f"no caption font found; set {_FONT_ENV} "
        f"(looked at: {', '.join(_FONT_CANDIDATES)})"
    )
    raise FileNotFoundError(msg)


def render(text: str, size: tuple[int, int], path: Path) -> Path:
    """The caption as a full-frame transparent PNG at `path`."""
    w, h = size
    bar_h = round(BAR_H * h)
    bar_y0 = round(BAR_CENTER * h - bar_h / 2)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rectangle(
        [0, bar_y0, w, bar_y0 + bar_h], fill=(0, 0, 0, round(BAR_ALPHA * 255))
    )
    font = ImageFont.truetype(find_font(), round(TEXT_BBOX_H * h / _FONT_BBOX_RATIO))
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((w - tw) / 2 - bbox[0], bar_y0 + (bar_h - th) / 2 - bbox[1]),
        text,
        font=font,
        fill="white",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    layer.save(path)
    return path
