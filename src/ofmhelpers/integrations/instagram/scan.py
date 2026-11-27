# Copyright (C) 2026 jpkrajewski

import logging
import time
from itertools import islice
from types import MappingProxyType
lazy from collections.abc import Iterator

from ofmhelpers.integrations.instagram.client import (
    InstagramClient,
    InstagramRateLimitedError,
    ProfilePrivateError,
)
from ofmhelpers.integrations.instagram.models import (
    Profile,
    ProfileReels,
    Reel,
    ReelMedia,
    ReelStat,
    ReelSummary,
)

logger = logging.getLogger(__name__)

_OLD_IN_A_ROW = 7


def _recent(reels: Iterator[ReelSummary], since: float) -> Iterator[ReelSummary]:
    old_in_a_row = 0
    for summary in reels:
        if summary.posted_at >= since:
            old_in_a_row = 0
            yield summary
        elif not summary.pinned:
            old_in_a_row += 1
            if old_in_a_row >= _OLD_IN_A_ROW:
                return


def _bulk_media(
    client: InstagramClient,
    username: str,
    codes: set[str],
    since: float,
) -> MappingProxyType[str, ReelMedia]:
    found: dict[str, ReelMedia] = {}
    old_in_a_row = 0
    try:
        for post in client.timeline(username):
            if post.code in codes and post.media is not None:
                found[post.code] = post.media
            old = post.posted_at < since and not post.pinned
            old_in_a_row = old_in_a_row + 1 if old else 0
            if found.keys() == codes or old_in_a_row >= _OLD_IN_A_ROW:
                break
    except InstagramRateLimitedError:
        logger.warning(
            "@%s: posts refused, reading reels one by one",
            username,
            exc_info=True,
        )
    return MappingProxyType(found)


def _reel(summary: ReelSummary, media: ReelMedia | None, profile: Profile) -> Reel:
    return Reel(
        code=summary.code,
        taken_at=media.taken_at if media else int(summary.posted_at),
        duration_s=media.duration_s if media else None,
        views=summary.play_count or 0,
        followers=profile.followers,
        thumbnail_url=summary.image_versions2.first,
        video_url=media.video_url if media else None,
    )


def scan_profile(  # noqa: PLR0913, PLR0917
    client: InstagramClient,
    username: str,
    min_views: int,
    max_duration_s: float,
    max_age_s: float,
    limit: int,
) -> ProfileReels:
    profile = client.open_profile(username)
    if profile.is_private:
        msg = "profile is private"
        raise ProfilePrivateError(msg)
    since = time.time() - max_age_s
    recent = list(islice(_recent(client.reels(profile.user_id), since), limit))
    viral = [s for s in recent if (s.play_count or 0) >= min_views]
    bulk = (
        _bulk_media(client, profile.username, {s.code for s in viral}, since)
        if viral
        else MappingProxyType({})
    )
    reels: list[Reel] = []
    for summary in viral:
        media = bulk.get(summary.code) or client.media(summary.pk)
        reel = _reel(summary, media, profile)
        if reel.duration_s is None or reel.duration_s <= max_duration_s:
            reels.append(reel)
    reels.sort(key=lambda r: r.views, reverse=True)
    return ProfileReels(
        profile=profile,
        scanned=len(recent),
        reels=reels,
        window_days=max_age_s / 86400,
        recent=[
            ReelStat(code=s.code, views=s.play_count or 0, posted_at=s.posted_at)
            for s in recent
        ],
    )
