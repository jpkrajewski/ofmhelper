"""How a finished video is exported: a registry of `Export`s, each turning
a file into its exported copy, plus `ExportOptions` that every export
honours. Pages offer `exports()` as one choice and each option as a switch;
a job stores the chosen `name` and the options.

- `original` — the file as generated.
- `capcut` — `utils.capcut`'s CapCut-style edit + export.
- one `DeviceExport` per `iphones/<model>/` profile — a camera recording of
  that phone; a new profile folder is a new choice with no code change.

A new kind of export is one `Export` subclass added to `_build`; a new
option is one `ExportOptions` field, applied by every `render`.
"""

from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.iphones import available, load_profile
from ofmhelpers.iphones.models import Recording
from ofmhelpers.iphones.recorder import record
from ofmhelpers.utils.capcut import DEFAULT_PRESET, apply_preset
from ofmhelpers.utils.ffmpeg import binary, run

__all__ = ["Export", "ExportOptions", "exports", "get_export"]


class ExportOptions(BaseModel):
    """Switches on top of any export; each field is one switch."""

    model_config = ConfigDict(frozen=True)

    # Flipped left-right.
    mirror: bool = False

    @property
    def suffix(self) -> str:
        """Added to the exported file's name, one part per switch that is on."""
        return "".join(f"_{name}" for name, on in self if on)


class Export(BaseModel, ABC):
    """One export choice: its stored `name`, the `label` a page shows."""

    model_config = ConfigDict(frozen=True)

    name: str
    label: str

    @abstractmethod
    def render(self, src: Path, options: ExportOptions) -> Path:
        """Write the exported copy of `src` next to it and return its path
        (`src` itself when nothing is written). Raises on failure."""

    def _dst(self, src: Path, options: ExportOptions, ext: str) -> Path:
        return src.with_name(f"{src.stem}_{self.name}{options.suffix}{ext}")


class OriginalExport(Export):
    """The file as generated; with a switch on, re-encoded with only that
    change (audio copied as is)."""

    def render(self, src: Path, options: ExportOptions) -> Path:
        if options == ExportOptions():
            return src
        dst = self._dst(src, options, src.suffix)
        vf = ["hflip"] if options.mirror else []
        run(
            [
                binary("ffmpeg"),
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-vf",
                ",".join(vf),
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-c:a",
                "copy",
                str(dst),
            ],
            settings.exports.timeout_s,
        )
        return dst


class CapcutExport(Export):
    def render(self, src: Path, options: ExportOptions) -> Path:
        dst = self._dst(src, options, ".mp4")
        preset = DEFAULT_PRESET.model_copy(update={"mirror": options.mirror})
        apply_preset(src, dst, preset, settings.exports.timeout_s)
        return dst


class DeviceExport(Export):
    """A recording of the `profile` phone, dated now in the configured time
    zone, filmed somewhere in the configured place."""

    profile: str

    def render(self, src: Path, options: ExportOptions) -> Path:
        cfg = settings.exports
        dst = self._dst(src, options, ".mov")
        recording = Recording.ending_now(
            ZoneInfo(cfg.device_timezone), cfg.device_place
        ).model_copy(update={"mirror": options.mirror})
        record(src, dst, load_profile(self.profile), recording, cfg.timeout_s)
        return dst


def _build() -> list[Export]:
    devices = [
        DeviceExport(name=p, label=load_profile(p).device.model, profile=p)
        for p in available()
    ]
    return [
        OriginalExport(name="original", label="Original"),
        CapcutExport(name="capcut", label="CapCut export"),
        *devices,
    ]


@lru_cache
def exports() -> dict[str, Export]:
    """Every export, by name, in the order a page lists them."""
    return {e.name: e for e in _build()}


def get_export(name: str) -> Export:
    try:
        return exports()[name]
    except KeyError:
        msg = f"unknown export {name!r}"
        raise ValueError(msg) from None
