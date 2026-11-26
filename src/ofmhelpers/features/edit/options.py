# Copyright (C) 2026 jpkrajewski

from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from ofmhelpers.media.size import Size as Ratio


class Aspect(StrEnum):
    ORIGINAL = "original"
    PORTRAIT = "9:16"
    FOUR_FIVE = "4:5"
    THREE_FOUR = "3:4"
    SQUARE = "1:1"
    LANDSCAPE = "16:9"

    @property
    def ratio(self) -> Ratio | None:
        if self is Aspect.ORIGINAL:
            return None
        w, h = self.value.split(":")
        return Ratio(width=int(w), height=int(h))

    @property
    def label(self) -> str:
        return "Original" if self is Aspect.ORIGINAL else self.value


class Size(StrEnum):
    P720 = "720"
    P1080 = "1080"
    P2160 = "2160"

    @property
    def short_side(self) -> int:
        return int(self.value)

    @property
    def label(self) -> str:
        return "4K" if self is Size.P2160 else f"{self.value}p"


SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def _a_speed(value: float) -> float:
    if value not in SPEEDS:
        msg = f"speed must be one of {', '.join(f'{s:g}' for s in SPEEDS)}"
        raise ValueError(msg)
    return value


Speed = Annotated[float, AfterValidator(_a_speed)]

TURNS = (0, 90, 180, 270)


def _a_turn(value: int) -> int:
    if value not in TURNS:
        msg = "turn must be 0, 90, 180 or 270"
        raise ValueError(msg)
    return value


Turn = Annotated[int, AfterValidator(_a_turn)]


class Framing(BaseModel):
    turn: Turn = 0
    mirror: bool = False
    aspect: Aspect = Aspect.ORIGINAL
    size: Size = Size.P1080
    scale: float = Field(default=1.0, ge=0.1, le=1, allow_inf_nan=False)
    x: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
