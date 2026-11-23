# Copyright (C) 2026 jpkrajewski

import re

IMAGES_FIRST = re.compile(
    r"instagram\.com/(?:[^/]+/)?p/"
    r"|(?:^|[/.])(?:x|twitter)\.com/"
    r"|pinterest\.|pin\.it/"
    r"|tiktok\.com/.*/photo/",
    re.IGNORECASE,
)

SAVER_POST = re.compile(
    r"instagram\.com/(?:[^/]+/)?(?:p|reels?|tv)/",
    re.IGNORECASE,
)


def images_first(url: str) -> bool:
    return IMAGES_FIRST.search(url) is not None


def saver_supported(url: str) -> bool:
    return SAVER_POST.search(url) is not None
