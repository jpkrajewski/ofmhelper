# Copyright (C) 2026 jpkrajewski

import re
import unicodedata
from urllib.parse import urlsplit

_INVISIBLE: frozendict[str, str] = frozendict({"Cc": " ", "Cf": ""})
_TRAILING = ",;"
_TLD = re.compile(r"\.(?:[^\W\d_]{2,}|xn--[a-z0-9-]+)\.?$", re.IGNORECASE)
_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_HOST_END = re.compile(r"[/?#]")


class NotALinkError(ValueError):
    def __init__(self) -> None:
        super().__init__("Paste links only, separated by spaces or new lines")


def _is_web_address(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:  # silent: not a web address
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def read_links(pasted: str) -> list[str]:
    links = []
    visible = "".join(
        _INVISIBLE.get(unicodedata.category(char), char) for char in pasted
    )
    for token in visible.split():
        link = token.rstrip(_TRAILING)
        if not link:
            continue
        host = _HOST_END.split(link, maxsplit=1)[0]
        named = _TLD.search(host) or _IPV4.fullmatch(host)
        if ":" not in host and "@" not in host and named:
            link = f"https://{link}"
        if not _is_web_address(link):
            raise NotALinkError
        links.append(link)
    return links
