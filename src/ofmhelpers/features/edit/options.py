"""What /edit offers besides the part itself: the crop shapes, the speeds
and the sizes (static data), and how a cut or a frame grab is framed.

A crop is a shape, how big the box is (`scale`: 1 = the biggest box of
that shape in the picture, its corners on the page make it smaller) and
where it sits in the room it has to move: `x` / `y` from 0 (left / top) to
1 (right / bottom), 0.5 centred. The page draws the box over the player;
ffmpeg cuts the same box out of the source (media/trim.py `crop_filter`).
Original at scale 1 is no crop. Before the crop the picture is turned:
`turn` degrees clockwise, then mirrored left to right (`mirror`), as the
page shows it; the crop box is drawn on the turned picture."""

from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field

from ofmhelpers.media.size import Size as Ratio


class Aspect(StrEnum):
    """The shapes the page offers, in its order."""

    ORIGINAL = "original"
    PORTRAIT = "9:16"
    FOUR_FIVE = "4:5"
    THREE_FOUR = "3:4"
    SQUARE = "1:1"
    LANDSCAPE = "16:9"

    @property
    def ratio(self) -> Ratio | None:
        """Width to height, None for the source's own shape."""
        if self is Aspect.ORIGINAL:
            return None
        w, h = self.value.split(":")
        return Ratio(width=int(w), height=int(h))

    @property
    def label(self) -> str:
        return "Original" if self is Aspect.ORIGINAL else self.value


class Size(StrEnum):
    """The short side a cut or a frame comes out at (scaled up or down),
    in the page's order. 4K is the most the edit worker's 2 GB holds: an 8K
    source cut to 4K peaked at 1.35 GB (2026-11-07)."""

    P720 = "720"
    P1080 = "1080"
    P2160 = "2160"

    @property
    def short_side(self) -> int:
        return int(self.value)

    @property
    def label(self) -> str:
        return "4K" if self is Size.P2160 else f"{self.value}p"


# Playback speeds a cut can have, in the page's order.
SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def _a_speed(value: float) -> float:
    if value not in SPEEDS:
        msg = f"speed must be one of {', '.join(f'{s:g}' for s in SPEEDS)}"
        raise ValueError(msg)
    return value


Speed = Annotated[float, AfterValidator(_a_speed)]

# Quarter turns clockwise. An int checked against these, not a Literal: a
# Literal refuses the "90" a query string carries (/edit/frame).
TURNS = (0, 90, 180, 270)


def _a_turn(value: int) -> int:
    if value not in TURNS:
        msg = "turn must be 0, 90, 180 or 270"
        raise ValueError(msg)
    return value


Turn = Annotated[int, AfterValidator(_a_turn)]


class Framing(BaseModel):
    """The turn, the crop (Original: none) and the size a cut or a frame
    grab is asked with."""

    turn: Turn = 0
    mirror: bool = False
    aspect: Aspect = Aspect.ORIGINAL
    size: Size = Size.P1080
    scale: float = Field(default=1.0, ge=0.1, le=1, allow_inf_nan=False)
    x: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
