# Copyright (C) 2026 jpkrajewski

from typing import Self
lazy import random

from pydantic import BaseModel, Field

from ofmhelpers.media.repurpose.transformations.constants import KnobConstants

Span = tuple[float, float]
Value = float | Span


def _further(value: float, factor: float, neutral: float) -> float:
    return neutral + (value - neutral) * factor


class Knobs(BaseModel, frozen=True):
    constants: KnobConstants = Field(default=KnobConstants(), exclude=True)

    def stronger(self, factor: float) -> Self:
        update: dict[str, Value | Knobs] = {}
        for name, value in self:
            if isinstance(value, Knobs):
                update[name] = value.stronger(factor)
            elif (
                value is not None
                and type(self).model_fields[name].annotation == Value | None
            ):
                neutral = self.constants.neutral.get(name, 0.0)
                update[name] = (
                    (
                        _further(value[0], factor, neutral),
                        _further(value[1], factor, neutral),
                    )
                    if isinstance(value, tuple)
                    else _further(value, factor, neutral)
                )
        return self.model_copy(update=update)


def draw(value: Value | None, rng: random.Random) -> float | None:
    if value is None:
        return None
    if isinstance(value, tuple):
        return rng.uniform(*value)
    return float(value)


def draw_int(value: Value | None, rng: random.Random) -> int | None:
    drawn = draw(value, rng)
    return None if drawn is None else round(drawn)


def pick(options: list[str] | str | None, rng: random.Random) -> str | None:
    if options is None or isinstance(options, str):
        return options
    return rng.choice(options) if options else None


def chance(probability: float | None, rng: random.Random) -> bool:
    return probability is not None and rng.random() < probability


def even(v: float) -> int:
    return max(2, round(v / 2) * 2)
