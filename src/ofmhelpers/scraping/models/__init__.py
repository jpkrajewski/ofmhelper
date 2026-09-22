"""Post models, one file per platform, plus the lead-hunt profile/lead
shapes. Import from here, not the submodules."""

from ofmhelpers.scraping.models.lead import Confidence, Lead
from ofmhelpers.scraping.models.post import PostBase, Reel
from ofmhelpers.scraping.models.profile import InstagramProfile
from ofmhelpers.scraping.models.tiktok import TikTokAuthor, TikTokVideo

__all__ = [
    "Confidence",
    "InstagramProfile",
    "Lead",
    "PostBase",
    "Reel",
    "TikTokAuthor",
    "TikTokVideo",
]
