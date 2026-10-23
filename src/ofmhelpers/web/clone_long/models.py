"""The data of a long clone: how a video is split, what it costs, what a
job stores. Pure values -- no I/O."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict


class Piece(BaseModel):
    """One stretch of the source video, in seconds."""

    model_config = ConfigDict(frozen=True)

    start: float
    length: float


class Plan(BaseModel):
    """A video as the fewest pieces the model takes, whole seconds and as
    equal as they can be (50 s at 15 s: 13 + 13 + 12 + 12, never
    15 + 15 + 15 + 5 -- a short tail would look unlike the rest)."""

    model_config = ConfigDict(frozen=True)

    pieces: tuple[Piece, ...]

    @classmethod
    def split(cls, seconds: float, max_s: int) -> Plan:
        if seconds <= max_s:
            return cls(pieces=(Piece(start=0, length=seconds),))
        count = math.ceil(seconds / max_s)
        base, extra = divmod(round(seconds), count)
        pieces = []
        start = 0
        for i in range(count):
            length = base + (1 if i < extra else 0)
            pieces.append(Piece(start=start, length=length))
            start += length
        # The fraction rounding dropped or added belongs to the last piece.
        last = pieces[-1]
        pieces[-1] = Piece(start=last.start, length=seconds - last.start)
        return cls(pieces=tuple(pieces))

    @property
    def count(self) -> int:
        return len(self.pieces)


class Shape(BaseModel):
    """A picture's width and height as it plays."""

    model_config = ConfigDict(frozen=True)

    width: int
    height: int

    @property
    def ratio(self) -> float:
        return self.width / self.height

    def nearest(self, ratios: list[str]) -> str:
        """Of `ratios` ("16:9", "9:16", ...), the one closest to this shape
        (by how many times wider or narrower, both ways alike)."""

        def distance(text: str) -> float:
            w, h = (int(x) for x in text.split(":"))
            return abs(math.log(self.ratio / (w / h)))

        return min(ratios, key=distance)

    def size_at(self, height: int) -> tuple[int, int]:
        """This shape `height` pixels tall, both sides even (H.264 needs it)."""
        even_height = height - height % 2
        width = round(even_height * self.ratio)
        return width - width % 2, even_height

    def within(self, side: int) -> tuple[int, int] | None:
        """This shape with its short side at most `side` (`size_at`); None
        when it already is."""
        if min(self.width, self.height) <= side:
            return None
        landscape = self.width >= self.height
        return self.size_at(side if landscape else round(side / self.ratio))


class Rate(BaseModel):
    """A provider's Wan 3.0 price: credits per second of source video by
    resolution, and what one credit costs."""

    model_config = ConfigDict(frozen=True)

    credits_per_s: dict[str, float]
    usd_per_credit: float

    def quote(self, seconds: float, resolution: str, pieces: int) -> Quote:
        total = seconds * self.credits_per_s.get(resolution, 0.0)
        return Quote(
            seconds=round(seconds, 1),
            pieces=pieces,
            credits=round(total, 1),
            usd=round(total * self.usd_per_credit, 2),
        )


class Quote(BaseModel):
    """What a run costs: billed for the whole length, piece by piece."""

    model_config = ConfigDict(frozen=True)

    seconds: float
    pieces: int
    credits: float
    usd: float

    @property
    def label(self) -> str:
        noun = "piece" if self.pieces == 1 else "pieces"
        return f"{self.seconds:.0f} s · {self.pieces} {noun} · ${self.usd:.2f}"


class LongCloneParams(BaseModel):
    """A run as its job stores it (shown on its page: never the API key).
    `seconds` is the source length measured at submit, so the worker and a
    recovery re-run cut the very same pieces."""

    provider: str
    model: str
    prompt: str
    resolution: str
    image: str
    video: str
    # The source's shape: every piece is asked at its nearest ratio and the
    # joined video has exactly its aspect.
    width: int
    height: int
    seconds: float
    pieces: int
    credits: float
    usd: float

    @property
    def shape(self) -> Shape:
        return Shape(width=self.width, height=self.height)
