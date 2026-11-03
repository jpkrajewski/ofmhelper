"""Stitch several images into one side-by-side character sheet."""

from pathlib import Path

from PIL import Image, ImageOps


def combine_side_by_side(sources: list[Path], dest: Path) -> Path:
    """Place `sources` left to right, in order, with no gap, and save the
    result losslessly as PNG at `dest`.

    Every panel is scaled to the tallest source's height (aspect ratio kept),
    so the sharpest input is never downscaled to match a smaller one. EXIF
    rotation is applied first, otherwise a phone photo lands sideways."""
    panels = []
    for src in sources:
        with Image.open(src) as img:
            panels.append(ImageOps.exif_transpose(img).convert("RGB"))

    height = max(p.height for p in panels)
    panels = [
        p
        if p.height == height
        else p.resize(
            (round(p.width * height / p.height), height), Image.Resampling.LANCZOS
        )
        for p in panels
    ]

    sheet = Image.new("RGB", (sum(p.width for p in panels), height))
    x = 0
    for panel in panels:
        sheet.paste(panel, (x, 0))
        x += panel.width
    sheet.save(dest, "PNG")
    return dest
