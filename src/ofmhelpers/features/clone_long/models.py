# Copyright (C) 2026 jpkrajewski

import math

from pydantic import BaseModel, ConfigDict


class Piece(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float
    length: float


class Plan(BaseModel):
    model_config = ConfigDict(frozen=True)

    pieces: tuple[Piece, ...]

    @classmethod
    def split(cls, seconds: float, max_s: int) -> Plan:
        if seconds <= max_s:
            return cls(pieces=(Piece(start=0, length=seconds),))
        count = math.ceil(seconds / max_s)
        front = min(
            float(max_s),
            max(float(max_s - 1), (seconds - max_s) / (count - 1)),
        )
        tail = seconds - front * (count - 1)
        if tail < max_s / 3:
            return cls._even(seconds, count, max_s)
        return cls._from_lengths(seconds, [front] * (count - 1) + [tail], max_s)

    @classmethod
    def _even(cls, seconds: float, count: int, max_s: int) -> Plan:
        base, extra = divmod(round(seconds), count)
        lengths = [float(base + (1 if i < extra else 0)) for i in range(count)]
        return cls._from_lengths(seconds, lengths, max_s)

    @classmethod
    def _from_lengths(cls, seconds: float, lengths: list[float], max_s: int) -> Plan:
        pieces = []
        start = 0.0
        for length in lengths:
            pieces.append(Piece(start=start, length=length))
            start += length
        last = pieces[-1]
        pieces[-1] = Piece(
            start=last.start,
            length=min(float(max_s), seconds - last.start),
        )
        return cls(pieces=tuple(pieces))

    @property
    def count(self) -> int:
        return len(self.pieces)


class Shape(BaseModel):
    model_config = ConfigDict(frozen=True)

    width: int
    height: int

    @property
    def ratio(self) -> float:
        return self.width / self.height

    def nearest(self, ratios: list[str]) -> str:

        def distance(text: str) -> float:
            w, h = (int(x) for x in text.split(":"))
            return abs(math.log(self.ratio / (w / h)))

        return min(ratios, key=distance)

    def size_at(self, height: int) -> Shape:
        even_height = height - height % 2
        width = round(even_height * self.ratio)
        return Shape(width=width - width % 2, height=even_height)

    def within(self, side: int) -> Shape | None:
        if min(self.width, self.height) <= side:
            return None
        landscape = self.width >= self.height
        return self.size_at(side if landscape else round(side / self.ratio))


class Rate(BaseModel):
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
    provider: str
    model: str
    prompt: str
    resolution: str
    image: str
    video: str
    width: int
    height: int
    seconds: float
    pieces: int
    credits: float
    usd: float
    chain: bool = False
    colour_match: bool = False
    audio: bool = False

    @property
    def shape(self) -> Shape:
        return Shape(width=self.width, height=self.height)
