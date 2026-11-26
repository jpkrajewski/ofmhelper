# Copyright (C) 2026 jpkrajewski

lazy from datetime import datetime

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.apiyi.models import PROVIDER


def slow_notice(now: datetime) -> str:
    s = settings.apiyi
    start, end = s.slow_from_utc_h, s.slow_until_utc_h
    inside = start <= now.hour < end if start <= end else not end <= now.hour < start
    if not inside:
        return ""
    return (
        f"{PROVIDER} can be slow right now. "
        f"Fastest from {{{end:02d}:00Z}} to {{{start:02d}:00Z}}, {{zone}} time"
    )
