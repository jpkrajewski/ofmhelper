# Copyright (C) 2026 jpkrajewski

from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit
lazy from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, field_validator

from ofmhelpers.config import settings
from ofmhelpers.features.clone_links.constants import clone_links_constants
from ofmhelpers.integrations.downloaders.generic import site_of


def _instagram(parts: SplitResult) -> str:
    found = clone_links_constants().instagram_post.match(parts.path)
    if found is None:
        return parts._replace(
            scheme="https",
            netloc="www.instagram.com",
            query="",
        ).geturl()
    return f"https://www.instagram.com/reel/{found[1]}/"


def _tiktok(parts: SplitResult) -> str:
    found = clone_links_constants().tiktok_video.match(parts.path)
    if found:
        return f"https://www.tiktok.com{found[0]}"
    return parts.geturl()


def _on(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _site(host: str) -> Callable[[SplitResult], str] | None:
    constants = clone_links_constants()
    if _on(host, constants.instagram_domain):
        return _instagram
    if _on(host, constants.tiktok_domain):
        return _tiktok
    return None


def site_name(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    names = clone_links_constants().site_names
    named = next((name for d, name in names.items() if _on(host, d)), None)
    if named:
        return named
    labels = host.removeprefix("www.").split(".")
    return labels[-2].capitalize() if len(labels) > 1 else "The site"


def _share_link(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    hosts = clone_links_constants().share_hosts
    return parts.path.startswith("/share/") and any(_on(host, d) for d in hosts)


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
        constants = clone_links_constants()
        text = str(value).strip().rstrip(constants.trailing)
        if "://" not in text:
            text = f"https://{text}"
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
        if parts.scheme.lower() not in ("http", "https") or not constants.tld.search(
            host,
        ):
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
    return (
        found in settings.clone_links.video_extractors
        or clone_links_constants().not_one_post
    )


def read_pasted(text: str) -> PastedLinks:
    return read_links(text, video_site=one_post)


def read_links(
    text: str,
    video_site: Callable[[str], bool | str] | None = None,
) -> PastedLinks:
    constants = clone_links_constants()
    links: list[str] = []
    bad: list[BadLine] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        tokens = [
            t
            for f in constants.token.findall(line)
            for t in constants.glued.split(f)
            if t
        ]
        found = [link for t in tokens if (link := _link(t))]
        if len(tokens) != 1 or not found:
            reason = _reason(len(found))
        elif _share_link(found[0]):
            reason = constants.share_link
        elif video_site is not None and (taken := video_site(found[0])) is not True:
            reason = taken if isinstance(taken, str) else constants.not_a_video_site
        else:
            links.append(found[0])
            continue
        bad.append(BadLine(number=number, text=line.strip(), reason=reason))
    unique = list(dict.fromkeys(links))
    return PastedLinks(links=unique, bad_lines=bad, repeated=len(links) - len(unique))
