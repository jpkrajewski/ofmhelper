"""What a clone costs and how long a run is asked to be: math on the table's
prices (`settings.web`, env-tunable) and a model's schema. No I/O."""

from pydantic import BaseModel

from ofmhelpers.config import WebSettings, settings
from ofmhelpers.features.clone.targets import CloneTarget, PriceRate, clone_targets
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec


class UnpricedError(ValueError):
    """No price for that provider or resolution: never a silent $0."""


class ClonePrice(BaseModel):
    """One provider's clone price, as the form's live estimate
    (clone-cost.js) reads it: credits per second of source video by
    resolution, what a credit costs, and the longest reference it takes.
    `unit`: what the provider bills in (its credits, or a currency)."""

    credits_per_video_s: dict[str, float]
    usd_per_credit: float
    max_reference_s: int
    note: str
    unit: str = "credits"

    def per_second(self, resolution: str) -> float:
        """A source second's `unit`s at `resolution`."""
        try:
            return self.credits_per_video_s[resolution]
        except KeyError:
            msg = f"No clone price at {resolution}"
            raise UnpricedError(msg) from None


def _price(target: CloneTarget, rate: PriceRate, web: WebSettings) -> ClonePrice:
    return ClonePrice(
        credits_per_video_s=getattr(web, rate.per_second),
        usd_per_credit=getattr(web, rate.usd_per_credit)
        if rate.usd_per_credit
        else 1.0,
        max_reference_s=target.max_reference_s,
        note=rate.note,
        unit=rate.unit,
    )


def clone_pricing() -> dict[str, ClonePrice]:
    """Every clone provider's price. A provider without one gets no estimate
    (the panel hides)."""
    web = settings.web
    return {
        name: _price(target, target.price, web)
        for name, target in clone_targets().items()
        if target.price
    }


def clone_price(provider: str) -> ClonePrice:
    """`provider`'s price; `UnpricedError` when it has none."""
    price = clone_pricing().get(provider)
    if price is None:
        msg = f"No clone price for {provider}"
        raise UnpricedError(msg)
    return price


def clip_duration(spec: ModelSpec, seconds: float) -> int:
    """A source video's length as the nearest duration `spec` accepts."""
    field = spec.properties["duration"]
    whole = round(seconds)
    low = int(field.minimum) if field.minimum is not None else 1
    high = int(field.maximum) if field.maximum is not None else max(whole, low)
    return min(max(whole, low), high)
