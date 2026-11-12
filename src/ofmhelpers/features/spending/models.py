"""What /spending shows: one amount per VA and period, as `spending.html`
reads it. Money is `Decimal` to the cent on the page; a period whose runs
have no known price shows no amount, never $0."""

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

    @property
    def estimate_label(self) -> str | None:
        """ "est." when the whole amount is estimated, "$3.00 est." when
        part of it is; None when the providers reported all of it."""
        if not self.estimated_usd:
            return None
        if self.estimated_usd == self.usd:
            return "est."
        return f"{_dollars(self.estimated_usd)} est."

    @property
    def unpriced_label(self) -> str | None:
        if not self.unpriced:
            return None
        return f"{self.unpriced} run{'' if self.unpriced == 1 else 's'} without a price"

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


class SpendingView(BaseModel):
    """/spending: each field is a name `spending.html` reads. `grouped`: an
    admin's page, every VA under its owner. `zone`: whose days these are."""

    model_config = ConfigDict(frozen=True)

    owners: list[OwnerSpend]
    totals: list[Column]
    grouped: bool
    zone: str
