import re
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit
lazy from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, field_validator

from ofmhelpers.config import settings
from ofmhelpers.integrations.downloaders.generic import site_of

_TOKEN = re.compile(r"[^\s,;<>\"']+")
_GLUED = re.compile(r"(?<!=)(?=https?://)", re.IGNORECASE)
_TRAILING = ".!?:)]}"
_TLD = re.compile(r"\.[a-z]{2,}$")
NOT_A_VIDEO_SITE = "Please put Instagram, TikTok, Twitter etc."
NOT_ONE_POST = "Paste a reel's link, not a profile or playlist"
SHARE_LINK = "Open the link and copy the reel's address"
_INSTAGRAM_POST = re.compile(
    r"^/(?!share/)(?:[\w.]+/)?(?:reels?(?!/audio/)|p|tv)/([\w-]+)"
)
_TIKTOK_VIDEO = re.compile(r"^/@[\w.-]+/video/\d+")
_SHARE_HOSTS = ("instagram.com", "facebook.com")


def _instagram(parts: SplitResult) -> str:
    found = _INSTAGRAM_POST.match(parts.path)
    if found is None:
        return parts._replace(
            scheme="https", netloc="www.instagram.com", query=""
        ).geturl()
    return f"https://www.instagram.com/reel/{found[1]}/"


def _tiktok(parts: SplitResult) -> str:
    found = _TIKTOK_VIDEO.match(parts.path)
    if found:
        return f"https://www.tiktok.com{found[0]}"
    return parts.geturl()


_SITES: dict[str, Callable[[SplitResult], str]] = {
    "instagram.com": _instagram,
    "tiktok.com": _tiktok,
}
_NAMES = {"tiktok.com": "TikTok", "youtube.com": "YouTube", "youtu.be": "YouTube"}


def _on(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _site(host: str) -> Callable[[SplitResult], str] | None:
    return next((fn for d, fn in _SITES.items() if _on(host, d)), None)


def site_name(url: str) -> str:
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
    number: int
    text: str
    reason: str


class PastedLinks(BaseModel):
    links: list[str]
    bad_lines: list[BadLine]
    repeated: int


def _link(token: str) -> str | None:
    try:
        return SourceLink(url=token).url
    except ValueError:  # silent: not a link
        return None


def _reason(links: int) -> str:
    if links > 1:
        return f"{links} links on one line"
    return "a link and other text" if links else "not a link"


def one_post(url: str) -> bool | str:
    found = site_of(url)
    if found is None:
        return False
    return found in settings.clone_links.video_extractors or NOT_ONE_POST


def read_pasted(text: str) -> PastedLinks:
    return read_links(text, video_site=one_post)


def read_links(
    text: str, video_site: Callable[[str], bool | str] | None = None
) -> PastedLinks:
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
