from urllib.parse import urlsplit


class NotALinkError(ValueError):
    def __init__(self) -> None:
        super().__init__("Paste links only, one per line")


def _is_web_address(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:  # silent: not a web address
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def read_links(pasted: str) -> list[str]:
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
