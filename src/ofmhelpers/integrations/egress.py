import ipaddress
import socket
from urllib.parse import urljoin, urlsplit
lazy from collections.abc import Mapping

import requests

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

MAX_REDIRECTS = 3
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class NotPublicError(requests.RequestException): ...


def _addresses(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", maxsplit=1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and (ip in _NAT64 or ip.sixtofour):
        return False
    return ip.is_global


def check_public(url: str) -> None:
    if settings.infra.fake_externals:
        return
    host = ""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        found = _addresses(host) if parts.scheme in ("http", "https") and host else []
        public = bool(found) and all(_is_public(a) for a in found)
    except OSError, ValueError:
        logger.warning("egress: %r not resolved", host, exc_info=True)
        public = False
    if not public:
        msg = f"host {host!r} is not on the public internet"
        raise NotPublicError(msg)


def get(
    url: str,
    *,
    timeout: float | None = None,
    headers: Mapping[str, str] | None = None,
    stream: bool = False,
) -> requests.Response:
    for _hop in range(MAX_REDIRECTS + 1):
        check_public(url)
        response = outbound.get(
            url,
            allow_redirects=False,
            timeout=timeout,
            headers=headers,
            stream=stream,
        )
        if not response.is_redirect:
            try:
                response.raise_for_status()
            except requests.RequestException:
                response.close()
                raise
            return response
        location = response.headers["location"]
        response.close()
        url = urljoin(url, location)
    msg = f"more than {MAX_REDIRECTS} redirects"
    raise requests.TooManyRedirects(msg)
