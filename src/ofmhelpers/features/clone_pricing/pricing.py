# Copyright (C) 2026 jpkrajewski

from types import MappingProxyType

from pydantic import BaseModel

from ofmhelpers.config import WebSettings, settings
from ofmhelpers.features.clone_pricing.targets import (
    CloneTarget,
    PriceRate,
    clone_targets,
)
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec


class UnpricedError(ValueError): ...


class ClonePrice(BaseModel):
    credits_per_video_s: dict[str, float]
    usd_per_credit: float
    max_reference_s: int
    note: str
    unit: str = "credits"

    def per_second(self, resolution: str) -> float:
        try:
            return self.credits_per_video_s[resolution]
        except KeyError:
            msg = f"No clone price at {resolution}"
            raise UnpricedError(msg) from None


def _price(target: CloneTarget, rate: PriceRate, web: WebSettings) -> ClonePrice:
    named = {name for name in (rate.per_second, rate.usd_per_credit) if name}
    prices = web.model_dump(include=named)
    return ClonePrice(
        credits_per_video_s=prices[rate.per_second],
        usd_per_credit=prices[rate.usd_per_credit] if rate.usd_per_credit else 1.0,
        max_reference_s=target.max_reference_s,
        note=rate.note,
        unit=rate.unit,
    )


def clone_pricing() -> MappingProxyType[str, ClonePrice]:
    web = settings.web
    return MappingProxyType(
        {
            name: _price(target, target.price, web)
            for name, target in clone_targets().items()
            if target.price
        },
    )


def clone_price(provider: str) -> ClonePrice:
    price = clone_pricing().get(provider)
    if price is None:
        msg = f"No clone price for {provider}"
        raise UnpricedError(msg)
    return price


def clip_duration(spec: ModelSpec, seconds: float) -> int:
    field = spec.properties["duration"]
    whole = round(seconds)
    low = int(field.minimum) if field.minimum is not None else 1
    high = int(field.maximum) if field.maximum is not None else max(whole, low)
    return min(max(whole, low), high)
