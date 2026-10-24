"""Which of a profile's recent reels went viral: posted in the last
`max_age_s`, at least `min_views` views, and short enough to clone.

Age and views come from the reels tab alone. The mp4 and length of the
survivors come from the posts timeline, twelve posts a request; only reels it
does not cover (an account hidden from logged-out visitors, a reel kept off
the grid) cost a request each through `client.media`."""

import time
from collections.abc import Iterator
from itertools import islice

from ofmhelpers.instagram.client import (
    InstagramClient,
    InstagramRateLimitedError,
    ProfilePrivateError,
)
from ofmhelpers.instagram.models import (
    Profile,
    ProfileReels,
    Reel,
    ReelMedia,
    ReelStat,
    ReelSummary,
)
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

# Old posts in a row that end a read (see _recent, _bulk_media): one more
# than the 6 reels a profile can pin, since Instagram does not always flag a
# pinned one -- fewer could be all pins, ending the read before the new
# reels after them. Seven fit in the first 12-post page, so it costs no
# extra request.
_OLD_IN_A_ROW = 7


def _recent(reels: Iterator[ReelSummary], since: float) -> Iterator[ReelSummary]:
    """The tab is newest first after its pinned reels; old ones are skipped,
    and `_OLD_IN_A_ROW` old unpinned reels in a row end it (a pinned reel is
    not always flagged as one)."""
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
    client: InstagramClient, username: str, codes: set[str], since: float
) -> dict[str, ReelMedia]:
    """What the timeline knows about `codes`, read until it has them all or
    is clearly past `since`. Newest first, but pinned posts head it and are
    not always flagged as pinned, so one old post does not end it -- only
    `_OLD_IN_A_ROW` of them do."""
    found: dict[str, ReelMedia] = {}
    old_in_a_row = 0
    try:
        for code, media, posted_at, pinned in client.timeline(username):
            if code in codes and media is not None:
                found[code] = media
            old_in_a_row = old_in_a_row + 1 if posted_at < since and not pinned else 0
            if found.keys() == codes or old_in_a_row >= _OLD_IN_A_ROW:
                break
    except InstagramRateLimitedError:
        # Instagram throttles the logged-out posts query on its own (2026-10-23:
        # refused while the reels tab and single posts answered): the rest go
        # reel by reel, and a refusal there is the scan's.
        logger.warning("@%s: posts refused, reading reels one by one", username)
    return found


def _reel(summary: ReelSummary, media: ReelMedia | None, profile: Profile) -> Reel:
    """A reel whose mp4 and length could not be resolved (gated, no working
    login) is kept without them, never dropped."""
    return Reel(
        code=summary.code,
        taken_at=media.taken_at if media else int(summary.posted_at),
        duration_s=media.duration_s if media else None,
        views=summary.play_count or 0,
        followers=profile.followers,
        thumbnail_url=summary.image_versions2.first,
        video_url=media.video_url if media else None,
    )


def scan_profile(
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
        else {}
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
