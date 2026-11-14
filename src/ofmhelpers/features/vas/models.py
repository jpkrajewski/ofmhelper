"""What /vas shows: one amount per VA and period, and one row per VA and
day (when they logged in and out, the reels they made, what the day cost),
as `vas.html` reads it. Money is `Decimal` to the cent on the page; a period
whose runs have no known price shows no amount, never $0."""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

_CENT = Decimal("0.01")


def _dollars(amount: Decimal) -> str:
    return f"${amount.quantize(_CENT):,}"


class Period(StrEnum):
    """The page's columns; a value is the name its sums come back under
    (`SpendPeriod.name`)."""

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
    """One period's runs: how many, the dollars known, how much of that is
    the app's estimate (the provider reported no cost), and how many runs
    had no price at all."""

    model_config = ConfigDict(frozen=True)

    runs: int = 0
    usd: Decimal = Decimal(0)
    estimated_usd: Decimal = Decimal(0)
    unpriced: int = 0

    @property
    def priced(self) -> bool:
        """Whether any of its runs has a price: only then is there an
        amount to show."""
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
    """One period of a row, or of the totals."""

    model_config = ConfigDict(frozen=True)

    period: Period
    amount: Amount


class VaSpend(BaseModel):
    """One VA's row: a column per period, in the page's order."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    name: str
    active: bool
    columns: list[Column]


class OwnerSpend(BaseModel):
    """One owner's VAs. `owner`: their name; None for VAs nobody owns."""

    model_config = ConfigDict(frozen=True)

    owner: str | None
    vas: list[VaSpend]


class VaDay(BaseModel):
    """One VA's one day, worded for the page. `logged_in` / `logged_out`:
    clock times in the reader's zone ("09:02"; "17:40 · idle" when the app
    logged them out for doing nothing; "Active 14:05" while they are still
    in today, the time being their last action; "—" for none)."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    name: str
    day: date
    logged_in: str
    logged_out: str
    reels: int
    spent: str

    @property
    def day_label(self) -> str:
        return self.day.strftime("%a %d %b")


class VasView(BaseModel):
    """/vas: each field is a name `vas.html` reads. `grouped`: an
    admin's page, every VA under its owner. `zone`: whose days these are.
    `days`: this month's days, newest first, VAs by name within a day."""

    model_config = ConfigDict(frozen=True)

    owners: list[OwnerSpend]
    totals: list[Column]
    days: list[VaDay]
    grouped: bool
    zone: str
