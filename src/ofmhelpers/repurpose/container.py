"""Same-length byte patches of a finished MP4 (no atom size or chunk offset
moves): what ffmpeg cannot write itself. Generalised from `utils.capcut`."""

import mmap
from pathlib import Path

# VisualSampleEntry (ISO 14496-12): compressorname sits 42 bytes past the
# box type; 32 bytes long.
_COMPRESSOR_NAME_OFFSET = 42
_COMPRESSOR_NAME_LEN = 32
_VIDEO_ENTRIES = (b"avc1", b"hvc1", b"hev1")


def _find(m: mmap.mmap, needle: bytes, start: int = 0) -> int:
    at = m.find(needle, start)
    if at < 0:
        msg = f"{needle!r} not found in the output"
        raise ValueError(msg)
    return at


def rename_key(m: mmap.mmap, placeholder: str, real: str) -> None:
    """Rename an mdta key in the keys atom (equal lengths only)."""
    if len(placeholder) != len(real):
        msg = f"key rename must keep the length: {placeholder!r} -> {real!r}"
        raise ValueError(msg)
    start = _find(m, b"mdta" + placeholder.encode()) + len(b"mdta")
    m[start : start + len(real)] = real.encode()


def blank_compressor_name(m: mmap.mmap) -> None:
    """Zero the video sample entry's compressorname ("Lavc libx264")."""
    stsd = _find(m, b"stsd")
    for entry_type in _VIDEO_ENTRIES:
        entry = m.find(entry_type, stsd)
        if entry >= 0:
            start = entry + len(entry_type) + _COMPRESSOR_NAME_OFFSET
            m[start : start + _COMPRESSOR_NAME_LEN] = bytes(_COMPRESSOR_NAME_LEN)
            return
    msg = "no video sample entry after stsd"
    raise ValueError(msg)


def patch(path: Path, rename_keys: dict[str, str], *, blank_compressor: bool) -> None:
    if not rename_keys and not blank_compressor:
        return
    with path.open("r+b") as f, mmap.mmap(f.fileno(), 0) as m:
        for placeholder, real in rename_keys.items():
            rename_key(m, placeholder, real)
        if blank_compressor:
            blank_compressor_name(m)
