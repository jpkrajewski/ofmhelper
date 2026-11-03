"""What a download box takes: one web address per line. Anything else never
becomes a job: a pasted API key would otherwise be stored in the job and tried
as a URL by every downloader (prod, 2026-11-02)."""

from urllib.parse import urlsplit


class NotALinkError(ValueError):
    """A line of the box is not a web address. Worded for the page."""

    def __init__(self) -> None:
        super().__init__("Paste links only, one per line")


def _is_web_address(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def read_links(pasted: str) -> list[str]:
    """The box's non-empty lines, stripped. A line missing its `https://` gets
    it when it starts with a host name. Raises NotALinkError on any other line:
    the message never repeats the line, which may be a secret."""
    links = []
    for line in (raw.strip() for raw in pasted.splitlines()):
        if not line:
            continue
        link = line
        if "://" not in link and "." in link.split("/", 1)[0] and ":" not in link:
            link = f"https://{link}"
        if " " in link or not _is_web_address(link):
            raise NotALinkError
        links.append(link)
    return links
