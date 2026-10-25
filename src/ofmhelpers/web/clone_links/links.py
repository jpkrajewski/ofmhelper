"""What Check makes of the pasted text: one link per line, cleaned and once
each, in the order pasted. A line that is not exactly one link of a video
site -- two glued together ("…/reel/A/https://…"), a link and a word, no
link at all, a page no downloader takes (the caller's `video_site`, yt-dlp's
`site_of`) -- is named, and Check refuses the paste (user decision
2026-10-25: never guess, never drop what was pasted, all of it in one
answer).

Cleaning: no #fragment, no share tracker in the query
(`settings.clone_links.tracking_params`), host lower case, and per site
(`_SITES`) the one canonical spelling of a post -- Instagram writes one reel
as /reel/, /reels/, /<user>/reel/, with or without www. -- so a reel pasted
twice in two spellings is one run, not two paid ones. A new site = one
entry in `_SITES`.
"""

import re
from collections.abc import Callable
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from ofmhelpers.config import settings

# Text between separators: a link never holds whitespace, a comma or quotes.
_TOKEN = re.compile(r"[^\s,;<>\"']+")
# Where a link glued onto another starts ("…/docshttps://claude.ai/new",
# "…/reel/A/https://…"): every http(s)://, except a query value
# ("?u=https://…", a redirect's target), part of its link.
_GLUED = re.compile(r"(?<!=)(?=https?://)", re.IGNORECASE)
# Sentence punctuation stuck to the end of a pasted link.
_TRAILING = ".!?:)]}"
# A host ends in a name of two letters or more ("e.g" is no host).
_TLD = re.compile(r"\.[a-z]{2,}$")
# A link of no video site, in the VA's words (user wording 2026-10-25).
NOT_A_VIDEO_SITE = "Please put Instagram, TikTok, Twitter etc."
_INSTAGRAM_POST = re.compile(r"^/(?:[\w.]+/)?(reels?|p|tv)/([\w-]+)")
_TIKTOK_VIDEO = re.compile(r"^/@[\w.-]+/video/\d+")


def _instagram(parts: SplitResult) -> str:
    """https://www.instagram.com/reel/<code>/ (or /p/, /tv/): what yt-dlp
    and the share sheet agree on; the query never matters."""
    found = _INSTAGRAM_POST.match(parts.path)
    if found is None:
        return parts._replace(netloc="www.instagram.com", query="").geturl()
    kind = "reel" if found[1] in ("reel", "reels") else found[1]
    return f"https://www.instagram.com/{kind}/{found[2]}/"


def _tiktok(parts: SplitResult) -> str:
    """A video page keeps only its path: its query is all share trackers."""
    if _TIKTOK_VIDEO.match(parts.path):
        return parts._replace(scheme="https", query="").geturl()
    return parts.geturl()


# Host (itself or a subdomain) -> its canonical form of a cleaned link.
_SITES: dict[str, Callable[[SplitResult], str]] = {
    "instagram.com": _instagram,
    "tiktok.com": _tiktok,
}


def _site(host: str) -> Callable[[SplitResult], str] | None:
    return next(
        (fn for d, fn in _SITES.items() if host == d or host.endswith(f".{d}")), None
    )


def _tracker(name: str) -> bool:
    for param in settings.clone_links.tracking_params:
        if param.endswith("*") and name.startswith(param[:-1]):
            return True
        if name == param:
            return True
    return False


class SourceLink(BaseModel):
    """One pasted link, cleaned (see the module docstring). Not a web link
    (no host with a dot, not http/https) = `ValueError`."""

    model_config = ConfigDict(frozen=True)

    url: str

    @field_validator("url", mode="before")
    @classmethod
    def _clean(cls, value: object) -> str:
        text = str(value).strip().rstrip(_TRAILING)
        if "://" not in text:
            text = f"https://{text}"
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        if parts.scheme.lower() not in ("http", "https") or not _TLD.search(host):
            msg = f"Not a link: {value!r}"
            raise ValueError(msg)
        query = parse_qsl(parts.query, keep_blank_values=True)
        parts = parts._replace(
            scheme=parts.scheme.lower(),
            netloc=parts.netloc.lower(),
            query=urlencode([(k, v) for k, v in query if not _tracker(k)]),
            fragment="",
        )
        site = _site(host)
        return site(parts) if site else parts.geturl()


class BadLine(BaseModel):
    """A pasted line that is not exactly one link (1-based `number`), and
    why in a few words."""

    number: int
    text: str
    reason: str


class PastedLinks(BaseModel):
    """The links in a pasted text, one per line, cleaned, each once, in the
    order pasted; `bad_lines`: the lines that were not exactly one link;
    `repeated`: links dropped as the same as one before."""

    links: list[str]
    bad_lines: list[BadLine]
    repeated: int


def _link(token: str) -> str | None:
    try:
        return SourceLink(url=token).url
    except ValueError:
        return None


def _reason(links: int) -> str:
    if links > 1:
        return f"{links} links on one line"
    return "a link and other text" if links else "not a link"


def read_links(
    text: str, video_site: Callable[[str], bool] | None = None
) -> PastedLinks:
    """`video_site(link)`: whether a downloader can get a video from the
    link's site (the router passes yt-dlp's `site_of`); False = bad line."""
    links: list[str] = []
    bad: list[BadLine] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        tokens = [t for f in _TOKEN.findall(line) for t in _GLUED.split(f) if t]
        found = [link for t in tokens if (link := _link(t))]
        if len(tokens) != 1 or not found:
            reason = _reason(len(found))
        elif video_site is not None and not video_site(found[0]):
            reason = NOT_A_VIDEO_SITE
        else:
            links.append(found[0])
            continue
        bad.append(BadLine(number=number, text=line.strip(), reason=reason))
    unique = list(dict.fromkeys(links))
    return PastedLinks(links=unique, bad_lines=bad, repeated=len(links) - len(unique))
