# Copyright (C) 2026 jpkrajewski

from functools import lru_cache

from pydantic import BaseModel


class FileKindConstants(BaseModel, frozen=True):
    heic_extensions: frozenset[str] = frozenset({".heic", ".heif"})
    multi_frame_formats: frozenset[str] = frozenset(
        {"GIF", "MPO", "PNG", "TIFF", "WEBP"},
    )
    sequential_frame_formats: frozenset[str] = frozenset({"GIF", "TIFF"})


@lru_cache(maxsize=1)
def file_kind_constants() -> FileKindConstants:
    return FileKindConstants()
