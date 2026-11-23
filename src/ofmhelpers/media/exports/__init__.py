# Copyright (C) 2026 jpkrajewski

from abc import ABC, abstractmethod
from functools import lru_cache
from types import MappingProxyType
from zoneinfo import ZoneInfo
lazy from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.media.capcut import DEFAULT_PRESET, apply_preset
from ofmhelpers.media.ffmpeg import binary, run
from ofmhelpers.media.recorder import available, load_profile
from ofmhelpers.media.recorder.models import Recording
from ofmhelpers.media.recorder.recorder import record

__all__ = ["Export", "ExportOptions", "exports", "get_export"]


class ExportOptions(BaseModel):
    model_config = ConfigDict(frozen=True)

    mirror: bool = False

    @property
    def suffix(self) -> str:
        return "".join(f"_{name}" for name, on in self if on)


class Export(BaseModel, ABC):
    model_config = ConfigDict(frozen=True)

    name: str
    label: str

    @abstractmethod
    def render(self, src: Path, options: ExportOptions) -> Path: ...

    def _dst(self, src: Path, options: ExportOptions, ext: str) -> Path:
        return src.with_name(f"{src.stem}_{self.name}{options.suffix}{ext}")


class OriginalExport(Export):
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
    profile: str

    def render(self, src: Path, options: ExportOptions) -> Path:
        cfg = settings.exports
        dst = self._dst(src, options, ".mov")
        recording = Recording.ending_now(
            ZoneInfo(cfg.device_timezone),
            cfg.device_place,
        ).model_copy(update={"mirror": options.mirror})
        record(src, dst, load_profile(self.profile), recording, cfg.timeout_s)
        return dst


def _device_label(profile: str) -> str:
    model = load_profile(profile).device.model
    return f"{model} 4K" if profile.endswith("_4k") else model


def _build() -> list[Export]:
    devices = [
        DeviceExport(name=p, label=_device_label(p), profile=p) for p in available()
    ]
    return [
        OriginalExport(name="original", label="Original"),
        CapcutExport(name="capcut", label="CapCut export"),
        *devices,
    ]


@lru_cache
def exports() -> MappingProxyType[str, Export]:
    return MappingProxyType({e.name: e for e in _build()})


def get_export(name: str) -> Export:
    try:
        return exports()[name]
    except KeyError:
        msg = f"unknown export {name!r}"
        raise ValueError(msg) from None
