from __future__ import annotations
from pathlib import Path
from PIL import Image

SUPPORTED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff")


def _next_counter(folder: Path) -> int:
    highest = 0
    for f in folder.iterdir():
        if f.stem.startswith("IMG_") and f.stem[4:].isdigit():
            highest = max(highest, int(f.stem[4:]))
    return highest + 1


def clean_metadata(path: Path) -> None:
    """Strip metadata from all supported images in `path`, renaming to IMG_XXXX.png in place."""
    path = Path(path)
    counter = _next_counter(path)

    for src in sorted(path.iterdir()):
        if src.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        dst = path / f"IMG_{counter:04d}.png"
        counter += 1

        with Image.open(src) as img:
            if img.mode in ("RGBA", "LA", "PA"):
                clean = Image.new("RGBA", img.size)
                clean.paste(img)
            else:
                clean = img.convert("RGB")
            clean.save(dst, "PNG", optimize=False)

        if src != dst:
            src.unlink()

        print(f"  {src.name} → {dst.name}")

    print("Done.")
