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


def images_first(url: str) -> bool:
    """True for a link of a kind of post that is usually photos."""
    return IMAGES_FIRST.search(url) is not None
