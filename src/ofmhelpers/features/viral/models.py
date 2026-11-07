"""/viral's outcome. The Apify reel scraper's items (`ApifyReel`,
`ApifyItemError`) live in `integrations/apify/instagram.py`, shared with the
/accounts Apify source."""

from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.integrations.apify.instagram import ApifyItemError, ApifyReel

__all__ = ["ApifyItemError", "ApifyReel", "SavedReel", "ViralFind"]


class SavedReel(BaseModel):
    reel: ApifyReel
    path: Path


class ViralFind(BaseModel):
    """A search's outcome: the top reels saved, most played first; how many
    reels were read and what the run cost."""

    reels: list[SavedReel]
    scanned: int
    cost_usd: float
