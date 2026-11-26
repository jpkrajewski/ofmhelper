# Copyright (C) 2026 jpkrajewski

lazy from pathlib import Path

from PIL import Image, ImageOps


def combine_side_by_side(sources: list[Path], dest: Path) -> Path:
    panels = []
    for src in sources:
        with Image.open(src) as img:
            panels.append(ImageOps.exif_transpose(img).convert("RGB"))

    height = max(p.height for p in panels)
    panels = [
        p
        if p.height == height
        else p.resize(
            (round(p.width * height / p.height), height),
            Image.Resampling.LANCZOS,
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
