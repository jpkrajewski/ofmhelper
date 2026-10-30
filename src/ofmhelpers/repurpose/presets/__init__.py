"""Ready-made presets: one JSON file each (`<name>.json` here); a new preset
is a new file, no code.

- `low`, `medium`, `hard`, `extreme` — legacy uniqify's Strength ranges
  (MINIMAL..EXTREME, its 2% boost included) + the handheld look; their
  `video_special` / `audio_special` / `device_special` parts are Duplyfi's
  transforms (the former Special switch), applied with `special=True`.
- `capcut` — `utils.capcut`'s default CapCut edit + jitter, with the
  `capcut` device.
- `stealth_wild` — the knob lab's pick (2026-10-26, repurpose/CLAUDE.md
  Lab): off-centre framing mix + vignette, pitch +-0.5 + flanger / phaser /
  vibrato; `strip` device. Best "looks the same" preset measured, still
  caught by SSCD.

A preset's `video` / `audio` hold `transformations.VideoSettings` /
`AudioSettings` knobs (values: `repurpose.values`).
"""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.repurpose.transformations.audio import AudioSettings
from ofmhelpers.repurpose.transformations.video import VideoSettings

_HERE = Path(__file__).parent


class Preset(BaseModel, frozen=True):
    label: str = ""
    video: VideoSettings = VideoSettings()
    audio: AudioSettings = AudioSettings()
    video_special: VideoSettings | None = None
    audio_special: AudioSettings | None = None
    # The device a ready-made preset is meant for (None: keep the current).
    device: str | None = None
    device_special: str | None = None


def preset_names() -> list[str]:
    return sorted(p.stem for p in _HERE.glob("*.json"))


@lru_cache
def load_preset(name_or_path: str) -> Preset:
    """A preset by name (`presets/<name>.json`) or a JSON file path."""
    path = Path(name_or_path)
    if path.suffix != ".json":
        path = _HERE / f"{name_or_path}.json"
    if not path.is_file():
        msg = f"unknown preset {name_or_path!r}; known: {', '.join(preset_names())}"
        raise ValueError(msg)
    return Preset.model_validate_json(path.read_text(encoding="utf-8"))
