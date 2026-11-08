"""Apify REST API (v2) shapes: the `data` envelope every answer comes in, a
run, and an account's usage limits."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _Api(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class RunStatus(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMING_OUT = "TIMING-OUT"
    TIMED_OUT = "TIMED-OUT"
    ABORTING = "ABORTING"
    ABORTED = "ABORTED"

    @property
    def finished(self) -> bool:
        return self not in (
            RunStatus.READY,
            RunStatus.RUNNING,
            RunStatus.TIMING_OUT,
            RunStatus.ABORTING,
        )


class _ChargeEvent(_Api):
    # Absent for tiered prices: the run is then priced by Apify's total.
    event_price_usd: float | None = Field(default=None, alias="eventPriceUsd")


class _PricingPerEvent(_Api):
    actor_charge_events: dict[str, _ChargeEvent] = Field(
        default_factory=dict, alias="actorChargeEvents"
    )


class _PricingInfo(_Api):
    pricing_per_event: _PricingPerEvent | None = Field(
        default=None, alias="pricingPerEvent"
    )


class ActorRun(_Api):
    id: str
    status: RunStatus
    default_dataset_id: str = Field(alias="defaultDatasetId")
    # Apify's total for the run; a pay-per-event actor's events reach it only
    # some time after the run ends (a 20-reel run: $0.001 when it turned
    # SUCCEEDED, $0.053 later).
    usage_total_usd: float = Field(default=0.0, alias="usageTotalUsd")
    charged_event_counts: dict[str, int] = Field(
        default_factory=dict, alias="chargedEventCounts"
    )
    pricing_info: _PricingInfo | None = Field(default=None, alias="pricingInfo")
    status_message: str | None = Field(default=None, alias="statusMessage")

    def priced(self, counts: dict[str, int]) -> float:
        """`counts` of this actor's events at their prices (an event
        without a price counts nothing)."""
        per_event = self.pricing_info.pricing_per_event if self.pricing_info else None
        events = per_event.actor_charge_events if per_event else {}
        return sum(
            count * (events[name].event_price_usd or 0.0)
            for name, count in counts.items()
            if name in events
        )

    @property
    def cost_usd(self) -> float:
        """What the run costs so far: its charged events at their prices, or
        Apify's total when that is more (or the prices are unknown). The
        charged counts lag too (a 71-reel run: 67 charged when it ended), so
        a caller that knows what the actor charges for prices that
        (`priced`)."""
        return max(self.priced(self.charged_event_counts), self.usage_total_usd)


class RunEnvelope(_Api):
    data: ActorRun


class _MonthlyLimit(_Api):
    max_monthly_usage_usd: float = Field(alias="maxMonthlyUsageUsd")


class _MonthlyUsage(_Api):
    monthly_usage_usd: float = Field(alias="monthlyUsageUsd")


class Limits(_Api):
    limits: _MonthlyLimit
    current: _MonthlyUsage

    @property
    def remaining_usd(self) -> float:
        return self.limits.max_monthly_usage_usd - self.current.monthly_usage_usd


class LimitsEnvelope(_Api):
    data: Limits


class ApiError(_Api):
    type: str = ""
    message: str = ""


class ErrorEnvelope(_Api):
    error: ApiError
