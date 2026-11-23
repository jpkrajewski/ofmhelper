# Copyright (C) 2026 jpkrajewski

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    event_price_usd: float | None = Field(default=None, alias="eventPriceUsd")


class _PricingPerEvent(_Api):
    actor_charge_events: dict[str, _ChargeEvent] = Field(
        default_factory=dict,
        alias="actorChargeEvents",
    )


class _PricingInfo(_Api):
    pricing_per_event: _PricingPerEvent | None = Field(
        default=None,
        alias="pricingPerEvent",
    )


class ActorRun(_Api):
    id: str
    status: RunStatus
    default_dataset_id: str = Field(alias="defaultDatasetId")
    usage_total_usd: float = Field(default=0.0, alias="usageTotalUsd")
    charged_event_counts: dict[str, int] = Field(
        default_factory=dict,
        alias="chargedEventCounts",
    )
    pricing_info: _PricingInfo | None = Field(default=None, alias="pricingInfo")
    status_message: str | None = Field(default=None, alias="statusMessage")

    def priced(self, counts: dict[str, int]) -> float:
        per_event = self.pricing_info.pricing_per_event if self.pricing_info else None
        events = per_event.actor_charge_events if per_event else {}
        return sum(
            count * (events[name].event_price_usd or 0.0)
            for name, count in counts.items()
            if name in events
        )

    @property
    def cost_usd(self) -> float:
        return max(self.priced(self.charged_event_counts), self.usage_total_usd)


class RunEnvelope(_Api):
    data: ActorRun


class DatasetItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    error: str | None = None
    url: str = ""

    @field_validator("error", "url", mode="before")
    @classmethod
    def _as_text(cls, value: object) -> object:
        return value if value is None else str(value)

    @property
    def failed(self) -> bool:
        return "error" in self.model_fields_set

    def read[T: BaseModel](self, model: type[T]) -> T:
        return model.model_validate(self.model_dump(exclude_unset=True))


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
