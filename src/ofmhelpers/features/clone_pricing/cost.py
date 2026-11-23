# Copyright (C) 2026 jpkrajewski

from decimal import Decimal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from ofmhelpers.db.jobs.schema import GenerationRecord, JobCost
from ofmhelpers.features.clone_pricing.pricing import UnpricedError, clone_price
from ofmhelpers.features.clone_pricing.targets import clone_targets

_CENT_PARTS = Decimal("0.0001")


class _PricedRun(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str
    model: str
    resolution: str
    seconds: float = Field(
        gt=0,
        validation_alias=AliasChoices("seconds", "source_seconds", "duration"),
    )


def _usd(amount: float) -> Decimal:
    return Decimal(str(amount)).quantize(_CENT_PARTS)


def _clone_estimate(params: dict) -> float | None:
    try:
        run = _PricedRun.model_validate(params)
    except ValidationError:  # silent: not a priced clone run
        return None
    target = clone_targets().get(run.provider)
    if target is None or target.model != run.model:
        return None
    try:
        price = clone_price(run.provider)
        return price.per_second(run.resolution) * price.usd_per_credit * run.seconds
    except UnpricedError:  # silent: no price: unknown, never 0
        return None


def cost_of(params: dict, generations: list[GenerationRecord]) -> JobCost:
    reported = [g.cost_usd for g in generations if g.cost_usd is not None]
    if reported:
        return JobCost(usd=_usd(sum(reported)))
    made = sum(1 for g in generations if g.state == "done")
    estimate = _clone_estimate(params) if made else None
    if estimate is None:
        return JobCost()
    return JobCost(usd=_usd(estimate * made / len(generations)), estimated=True)
