"""When APIYI's video runs are slow: Alibaba's Beijing servers fetch our
references over a line that crawls at China's evening peak (prod 2026-11-10
and 2026-11-12). No I/O."""

from datetime import datetime

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.apiyi.models import PROVIDER


def slow_notice(now: datetime) -> str:
    """What the provider select says at `now` (UTC): inside the slow hours,
    that it can be slow and the hours it is fastest; "" outside them. A time
    is written `{HH:MMZ}` (UTC) and the zone's name `{zone}`: the page shows
    them in the zone its header clock is set to (`Provider.notice`)."""
    s = settings.apiyi
    start, end = s.slow_from_utc_h, s.slow_until_utc_h
    inside = start <= now.hour < end if start <= end else not end <= now.hour < start
    if not inside:
        return ""
    return (
        f"{PROVIDER} can be slow right now. "
        f"Fastest from {{{end:02d}:00Z}} to {{{start:02d}:00Z}}, {{zone}} time"
    )
