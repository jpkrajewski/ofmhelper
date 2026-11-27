# Copyright (C) 2026 jpkrajewski

from enum import StrEnum


class SpoofRefusal(StrEnum):
    NOT_YOUR_MODEL = "Pick one of your models"


class MirrorChoice(StrEnum):
    RANDOM = "random"
    MIRRORED = "mirrored"
    NOT_MIRRORED = "not_mirrored"

    @property
    def mirror_chance(self) -> float | None:
        if self is MirrorChoice.MIRRORED:
            return 1.0
        if self is MirrorChoice.NOT_MIRRORED:
            return 0.0
        return None
