# Copyright (C) 2026 jpkrajewski

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.media.repurpose.transformations.audio import AudioSettings
from ofmhelpers.media.repurpose.transformations.video import VideoSettings

_HERE = Path(__file__).parent


class Preset(BaseModel, frozen=True):
    label: str = ""
    video: VideoSettings = VideoSettings()
    audio: AudioSettings = AudioSettings()
    video_special: VideoSettings | None = None
    audio_special: AudioSettings | None = None
    device: str | None = None
    device_special: str | None = None


def preset_names() -> list[str]:
    return sorted(p.stem for p in _HERE.glob("*.json"))


@lru_cache
def load_preset(name_or_path: str) -> Preset:
    path = Path(name_or_path)
    if path.suffix != ".json":
        path = _HERE / f"{name_or_path}.json"
    if not path.is_file():
        msg = f"unknown preset {name_or_path!r}; known: {', '.join(preset_names())}"
        raise ValueError(msg)
    return Preset.model_validate_json(path.read_text(encoding="utf-8"))
