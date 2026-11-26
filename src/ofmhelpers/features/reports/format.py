# Copyright (C) 2026 jpkrajewski

from datetime import datetime, timedelta

NONE = "—"
_THOUSAND = 1000
_MILLION = 1_000_000
_PRECISE_BELOW = 10
_DOLLAR = 100
_WHOLE_FROM_CENTS = 100 * _DOLLAR
_DAY_SLACK = timedelta(hours=1)


def _trim(text: str) -> str:
    return text.removesuffix(".0")


def compact(n: int | None) -> str:
    if n is None:
        return NONE
    if n < _THOUSAND:
        return str(n)
    if n < _MILLION:
        return _trim(f"{n / _THOUSAND:.1f}") + "k"
    return _trim(f"{n / _MILLION:.1f}") + "M"


def money(cents: int | None) -> str:
    if cents is None:
        return NONE
    if cents >= _WHOLE_FROM_CENTS:
        return f"${cents / _DOLLAR:,.0f}"
    return "$" + f"{cents / _DOLLAR:,.2f}".removesuffix(".00")


def percent(value: float | None) -> str:
    if value is None:
        return NONE
    if value >= _PRECISE_BELOW:
        return f"{value:.0f}%"
    return _trim(f"{value:.1f}") + "%"


def window_title(since: datetime, until: datetime) -> str:
    if abs((until - since) - timedelta(days=1)) <= _DAY_SLACK:
        return "Last 24H"
    return f"Since {since.day} {since:%b %H:%M}"
