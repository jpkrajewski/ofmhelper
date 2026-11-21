import json
from collections.abc import Collection
from pathlib import Path

from PIL import Image
from pydantic import BaseModel

from ofmhelpers.log import get_logger

logger = get_logger(__name__)
SUPPORTED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff")
MANIFEST_NAME = ".clean_manifest.json"


class CleanManifest(BaseModel):
    entries: dict[str, str] = {}


def _load_manifest(path: Path) -> CleanManifest:
    manifest = path / MANIFEST_NAME
    if not manifest.is_file():
        return CleanManifest()
    return CleanManifest(entries=json.loads(manifest.read_text(encoding="utf-8")))


def _store_manifest(path: Path, manifest: CleanManifest) -> None:
    temp = path / f"{MANIFEST_NAME}.tmp"
    temp.write_text(json.dumps(manifest.entries), encoding="utf-8")
    temp.replace(path / MANIFEST_NAME)


def _next_counter(folder: Path, entries: dict[str, str]) -> int:
    names = [f.stem for f in folder.iterdir()] + [
        Path(d).stem for d in entries.values()
    ]
    numbers = [int(n[4:]) for n in names if n.startswith("IMG_") and n[4:].isdigit()]
    return max(numbers, default=0) + 1


def _save_clean_png(src: Path, dst: Path) -> None:
    with Image.open(src) as img:
        if img.mode in ("RGBA", "LA", "PA"):
            clean = Image.new("RGBA", img.size)
            clean.paste(img)
        else:
            clean = img.convert("RGB")
    temp = dst.with_name(f"{dst.name}.tmp")
    clean.save(temp, "PNG", optimize=False)
    temp.replace(dst)


def clean_metadata(path: Path, only: Collection[str] | None = None) -> list[Path]:
    path = Path(path)
    manifest = _load_manifest(path)
    entries = manifest.entries
    outputs = set(entries.values())
    counter = _next_counter(path, entries)

    for src in sorted(path.iterdir()):
        if src.suffix.lower() not in SUPPORTED_EXTENSIONS or src.name in outputs:
            continue
        if only is not None and src.name not in only:
            continue

        if src.name not in entries:
            entries[src.name] = f"IMG_{counter:04d}.png"
            counter += 1
            _store_manifest(path, manifest)
        dst = path / entries[src.name]
        if not dst.is_file():
            _save_clean_png(src, dst)
        src.unlink()
        logger.info("%s -> %s", src.name, dst.name)

    logger.info("done")
    return [path / d for d in entries.values() if (path / d).is_file()]
