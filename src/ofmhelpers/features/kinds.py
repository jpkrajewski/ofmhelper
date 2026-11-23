# Copyright (C) 2026 jpkrajewski

from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}


VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}


AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg"}


def classify_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"
