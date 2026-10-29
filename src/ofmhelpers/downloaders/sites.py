"""What a link's post is likely to hold, from the URL alone (no request).
Shared by `media.py` (which downloader goes first) and `fake.py` (the fake
look at a link agrees with the fake download); import-free so neither
pulls the other in."""

import re

# Links whose posts are mostly photos or carousels: an Instagram post (a
# reel is /reel/), an X/Twitter status, a Pinterest pin, a TikTok photo post.
# gallery-dl gets every item of such a post, yt-dlp only its videos.
IMAGES_FIRST = re.compile(
    r"instagram\.com/(?:[^/]+/)?p/"
    r"|(?:^|[/.])(?:x|twitter)\.com/"
    r"|pinterest\.|pin\.it/"
    r"|tiktok\.com/.*/photo/",
    re.IGNORECASE,
)

# What the saver sites (downloaders/savers.py) take: one Instagram post.
# A profile, story tray or highlight is not a post -- savers answer those
# with avatars or nothing, so the fallback never tries them.
SAVER_POST = re.compile(
    r"instagram\.com/(?:[^/]+/)?(?:p|reels?|tv)/",
    re.IGNORECASE,
)


def images_first(url: str) -> bool:
    """True for a link of a kind of post that is usually photos."""
    return IMAGES_FIRST.search(url) is not None


def saver_supported(url: str) -> bool:
    """True for a link a saver site can fetch: an Instagram post."""
    return SAVER_POST.search(url) is not None
