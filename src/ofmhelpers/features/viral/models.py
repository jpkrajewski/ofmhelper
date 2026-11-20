from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.integrations.apify.instagram import ApifyItemError, ApifyReel

__all__ = ["ApifyItemError", "ApifyReel", "SavedReel", "ViralFind"]


class SavedReel(BaseModel):
    reel: ApifyReel
    path: Path


class ViralFind(BaseModel):
    reels: list[SavedReel]
    scanned: int
    cost_usd: float
