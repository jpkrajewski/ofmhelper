"""What a job's run cost in dollars, from its provider requests.

A provider that reports what a request cost (kie.ai, Elyum) is believed:
the job's cost is the sum. One that reports nothing (APIYI, Higgsfield,
RunningHub) gets the app's own price for the run where it has one: the
clone price table (`features/clone/pricing.py`, per second of source video
by resolution: what /clone's cost panel shows), so only a run of a
provider's clone model whose params name its resolution and source length.
Anything else has no known price: None, never 0. No I/O."""

from decimal import Decimal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from ofmhelpers.features.clone.pricing import UnpricedError, clone_price
from ofmhelpers.features.clone.targets import clone_targets
from ofmhelpers.schemas.persistence import GenerationRecord, JobCost

_CENT_PARTS = Decimal("0.0001")


class _PricedRun(BaseModel):
    """What the clone price needs of a job's params. `seconds`: the source
    video's length, under the name each clone tool stores it (/clone-long,
    /clone-links, /clone)."""

    model_config = ConfigDict(extra="ignore")

    provider: str
    model: str
    resolution: str
    seconds: float = Field(
        gt=0, validation_alias=AliasChoices("seconds", "source_seconds", "duration")
    )


def _usd(amount: float) -> Decimal:
    return Decimal(str(amount)).quantize(_CENT_PARTS)


def _clone_estimate(params: dict) -> float | None:
    """The clone price of a run with these params; None when they are not a
    priced clone run (another model, no price, no resolution or length)."""
    try:
        run = _PricedRun.model_validate(params)
    except ValidationError:
        return None
    target = clone_targets().get(run.provider)
    if target is None or target.model != run.model:
        return None
    try:
        price = clone_price(run.provider)
        return price.per_second(run.resolution) * price.usd_per_credit * run.seconds
    except UnpricedError:
        return None


def cost_of(params: dict, generations: list[GenerationRecord]) -> JobCost:
    """The cost of a job with these `params` and provider requests: what
    the provider reported, summed; else the clone price for the share of
    its requests that were made (`done`), flagged as an estimate; else no
    price."""
    reported = [g.cost_usd for g in generations if g.cost_usd is not None]
    if reported:
        return JobCost(usd=_usd(sum(reported)))
    made = sum(1 for g in generations if g.state == "done")
    estimate = _clone_estimate(params) if made else None
    if estimate is None:
        return JobCost()
    return JobCost(usd=_usd(estimate * made / len(generations)), estimated=True)
