"""What Check makes of the pasted text: one link per line, cleaned and once
each, in the order pasted. A line that is not exactly one link of a video
site -- two glued together ("…/reel/A/https://…"), a link and a word, no
link at all, a share link that only redirects, a page no downloader takes
or a profile/playlist (`read_pasted`: yt-dlp's `site_of` must be one of
`settings.clone_links.video_extractors`) -- is named, and Check refuses the
paste (user decision 2026-10-25: never guess, never drop what was pasted,
all of it in one answer).

Cleaning: no #fragment, no share tracker in the query
(`settings.clone_links.tracking_params`), host lower case, and per site
(`_SITES`) the one canonical spelling of a post -- Instagram writes one reel
as /reel/, /reels/, /p/, /tv/, /<user>/reel/, with or without www.; TikTok
one video on tiktok.com, m. or www. -- so a reel pasted twice in two
spellings is one run, not two paid ones. A new site = one entry in
`_SITES` (and `_NAMES` when its name is not its domain's).
"""

import re
from collections.abc import Callable
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from ofmhelpers.config import settings
from ofmhelpers.downloaders.generic import site_of

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
# A link of a video site that is not one post: a profile, playlist, channel,
# story tray, tag, sound, or a redirector.
NOT_ONE_POST = "Paste a reel's link, not a profile or playlist"
# A share-sheet link (instagram.com/share/...): a token that only redirects
# to the post, no shortcode in it (yt-dlp refuses it too).
SHARE_LINK = "Open the link and copy the reel's address"
# A post of any kind (reel, photo post, IGTV), with or without /<user>/ in
# front -- never /share/ (its token is no shortcode) nor a reel's audio page.
_INSTAGRAM_POST = re.compile(
    r"^/(?!share/)(?:[\w.]+/)?(?:reels?(?!/audio/)|p|tv)/([\w-]+)"
)
_TIKTOK_VIDEO = re.compile(r"^/@[\w.-]+/video/\d+")
# Sites whose /share/... links only redirect to a post.
_SHARE_HOSTS = ("instagram.com", "facebook.com")


def _instagram(parts: SplitResult) -> str:
    """https://www.instagram.com/reel/<code>/ for every kind of post: one
    shortcode is one post whatever the path says, yt-dlp reads it as a reel
    (a photo post ends as "no video"); the query never matters."""
    found = _INSTAGRAM_POST.match(parts.path)
    if found is None:
        return parts._replace(
            scheme="https", netloc="www.instagram.com", query=""
        ).geturl()
    return f"https://www.instagram.com/reel/{found[1]}/"


def _tiktok(parts: SplitResult) -> str:
    """A video page is https://www.tiktok.com/@<user>/video/<id> (the only
    host yt-dlp takes; tiktok.com and m. are the same page), nothing after
    it: its query is all share trackers. Short links (vm., vt.) as pasted:
    only a request could tell which video they are."""
    found = _TIKTOK_VIDEO.match(parts.path)
    if found:
        return f"https://www.tiktok.com{found[0]}"
    return parts.geturl()


# Host (itself or a subdomain) -> its canonical form of a cleaned link.
_SITES: dict[str, Callable[[SplitResult], str]] = {
    "instagram.com": _instagram,
    "tiktok.com": _tiktok,
}
# A site's name in messages, when it is not its domain's ("x.com" -> "X").
_NAMES = {"tiktok.com": "TikTok", "youtube.com": "YouTube", "youtu.be": "YouTube"}


def _on(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _site(host: str) -> Callable[[SplitResult], str] | None:
    return next((fn for d, fn in _SITES.items() if _on(host, d)), None)


def site_name(url: str) -> str:
    """The link's site as a VA names it: "Instagram", "TikTok", "YouTube"."""
    host = (urlsplit(url).hostname or "").lower()
    named = next((name for d, name in _NAMES.items() if _on(host, d)), None)
    if named:
        return named
    labels = host.removeprefix("www.").split(".")
    return labels[-2].capitalize() if len(labels) > 1 else "The site"


def _share_link(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.path.startswith("/share/") and any(_on(host, d) for d in _SHARE_HOSTS)


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


def one_post(url: str) -> bool | str:
    """Whether yt-dlp reads `url` as ONE post: its extractor (`site_of`)
    is one of `settings.clone_links.video_extractors` (an allowlist: yt-dlp
    keeps adding list extractors). False: no site's extractor takes it; else
    `NOT_ONE_POST` for a profile/playlist/story/tag/redirector link."""
    found = site_of(url)
    if found is None:
        return False
    return found in settings.clone_links.video_extractors or NOT_ONE_POST


def read_pasted(text: str) -> PastedLinks:
    """What /clone-links Check, previews and Generate take: `read_links`
    with every link one post of a video site (`one_post`)."""
    return read_links(text, video_site=one_post)


def read_links(
    text: str, video_site: Callable[[str], bool | str] | None = None
) -> PastedLinks:
    """`video_site(link)`: True when a downloader can get the video, False
    when no site of the link's has one (`NOT_A_VIDEO_SITE`), or why else
    the link is refused, in the VA's words. A share link that only
    redirects is refused before it is asked (`SHARE_LINK`)."""
    links: list[str] = []
    bad: list[BadLine] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        tokens = [t for f in _TOKEN.findall(line) for t in _GLUED.split(f) if t]
        found = [link for t in tokens if (link := _link(t))]
        if len(tokens) != 1 or not found:
            reason = _reason(len(found))
        elif _share_link(found[0]):
            reason = SHARE_LINK
        elif video_site is not None and (taken := video_site(found[0])) is not True:
            reason = taken if isinstance(taken, str) else NOT_A_VIDEO_SITE
        else:
            links.append(found[0])
            continue
        bad.append(BadLine(number=number, text=line.strip(), reason=reason))
    unique = list(dict.fromkeys(links))
    return PastedLinks(links=unique, bad_lines=bad, repeated=len(links) - len(unique))
