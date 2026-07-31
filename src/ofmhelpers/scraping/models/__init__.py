"""Post models, one file per platform. Import from here, not the submodules."""

from ofmhelpers.scraping.models.post import PostBase, Reel
from ofmhelpers.scraping.models.tiktok import TikTokAuthor, TikTokVideo

__all__ = ["PostBase", "Reel", "TikTokAuthor", "TikTokVideo"]
