from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

_CENT = Decimal("0.01")


def _dollars(amount: Decimal) -> str:
    return f"${amount.quantize(_CENT):,}"


class ShownZone(Protocol):
    label: str
    zone: str


class Period(StrEnum):
    TODAY = "today"
    WEEK = "week"
    MONTH = "month"

    @property
    def label(self) -> str:
        match self:
            case Period.TODAY:
                return "Today"
            case Period.WEEK:
                return "Last 7 days"
            case Period.MONTH:
                return "This month"


class Amount(BaseModel):
    model_config = ConfigDict(frozen=True)

    runs: int = 0
    usd: Decimal = Decimal(0)
    estimated_usd: Decimal = Decimal(0)
    unpriced: int = 0

    @property
    def priced(self) -> bool:
        return self.runs > self.unpriced

    @property
    def dollars(self) -> str:
        return _dollars(self.usd) if self.priced else "—"

    @property
    def runs_label(self) -> str:
        return f"{self.runs} run{'' if self.runs == 1 else 's'}"

    def plus(self, other: Amount) -> Amount:
        return Amount(
            runs=self.runs + other.runs,
            usd=self.usd + other.usd,
            estimated_usd=self.estimated_usd + other.estimated_usd,
            unpriced=self.unpriced + other.unpriced,
        )


class Column(BaseModel):
    model_config = ConfigDict(frozen=True)

    period: Period
    amount: Amount


class VaSpend(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    name: str
    active: bool
    columns: list[Column]


class OwnerSpend(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: str | None
    vas: list[VaSpend]


class VaDay(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int
    name: str
    day: date
    logged_in: str
    logged_out: str
    hours: str
    reels: int
    spent: str

    @property
    def day_label(self) -> str:
        return self.day.strftime("%a %d %b")


class VasView(BaseModel):
    model_config = ConfigDict(frozen=True)

    owners: list[OwnerSpend]
    totals: list[Column]
    days: list[VaDay]
    grouped: bool
    zone: str
