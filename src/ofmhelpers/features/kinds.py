# Copyright (C) 2026 jpkrajewski

from enum import StrEnum, auto
from pathlib import Path
from typing import Self

from ofmhelpers.features.constants import file_kind_constants


class FileKind(StrEnum):
    IMAGE = auto()
    VIDEO = auto()
    AUDIO = auto()
    OTHER = auto()

    @property
    def extensions(self) -> frozenset[str]:
        match self:
            case FileKind.IMAGE:
                return frozenset(
                    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"},
                ).union(file_kind_constants().heic_extensions)
            case FileKind.VIDEO:
                return frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})
            case FileKind.AUDIO:
                return frozenset({".mp3", ".wav", ".m4a", ".ogg"})
            case FileKind.OTHER:
                return frozenset()

    @classmethod
    def from_ext(cls, name: str) -> Self:
        extension = Path(name).suffix.lower()
        for kind in (cls.IMAGE, cls.VIDEO, cls.AUDIO):
            if extension in kind.extensions:
                return cls(kind)
        return cls.OTHER
