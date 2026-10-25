"""A kept cover is fetched only from the public internet: the URL comes
from a page yt-dlp read (any site's, on /clone-links), so a crafted page
must not point the server at its own network -- not at first, not through
a redirect."""

import io
import socket

import pytest
import requests
from PIL import Image

from ofmhelpers.web import reel_thumbs

PUBLIC = "157.240.1.1"
COVER = "https://cdn.example.com/cover.jpg"


def _jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1080, 1920), "teal").save(out, "JPEG")
    return out.getvalue()


def _reply(status: int = 200, location: str | None = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = _jpeg() if status == 200 else b""
    if location:
        response.headers["location"] = location
    return response


@pytest.fixture
def net(monkeypatch):
    """The network as kept_thumb sees it: `hosts` (host -> its addresses;
    missing = does not resolve) and `replies` (URL -> response); every GET
    asked is recorded with its keyword arguments."""
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "0")
    hosts: dict[str, list[str]] = {"cdn.example.com": [PUBLIC]}
    replies: dict[str, requests.Response] = {COVER: _reply()}
    asked: list[tuple[str, dict]] = []

    def addresses(host: str) -> list[str]:
        if host not in hosts:
            raise OSError(host)
        return hosts[host]

    def get(url: str, **kwargs) -> requests.Response:
        asked.append((url, kwargs))
        return replies[url]

    monkeypatch.setattr(reel_thumbs, "_addresses", addresses)
    monkeypatch.setattr(reel_thumbs.outbound, "get", get)
    return hosts, replies, asked


def test_a_public_cover_is_fetched_without_following_redirects_blindly(net, tmp_path):
    _hosts, _replies, asked = net
    dest = tmp_path / "c.webp"

    assert reel_thumbs.kept_thumb(dest, COVER) == dest

    assert dest.is_file()
    assert [(url, kw["allow_redirects"]) for url, kw in asked] == [(COVER, False)]


@pytest.mark.parametrize(
    "address",
    ["10.0.0.5", "172.18.0.3", "192.168.1.2", "127.0.0.1", "169.254.169.254", "::1"],
)
def test_a_host_on_a_private_network_is_never_asked(net, tmp_path, address):
    hosts, _replies, asked = net
    hosts["cdn.example.com"] = [address]
    dest = tmp_path / "c.webp"

    assert reel_thumbs.kept_thumb(dest, COVER) is None

    assert asked == []
    assert not dest.exists()


def _resolver(monkeypatch, *resolved: tuple) -> None:
    """getaddrinfo answering `resolved` (IPv4 and IPv6 sockaddrs)."""
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "0")
    answer = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", a) for a in resolved]
    monkeypatch.setattr(socket, "getaddrinfo", lambda _host, _port: answer)


def test_a_host_with_only_global_addresses_passes_the_resolver(monkeypatch):
    _resolver(monkeypatch, ("151.101.1.1", 0), ("2a04:4e42::1", 0, 0, 0))
    reel_thumbs._check_public(COVER)


@pytest.mark.parametrize(
    "resolved",
    [
        [("151.101.1.1", 0), ("::1", 0, 0, 0)],
        [("fe80::1%eth0", 0, 0, 2)],
        [("100.64.0.1", 0)],
    ],
)
def test_any_non_global_address_from_the_resolver_is_refused(monkeypatch, resolved):
    _resolver(monkeypatch, *resolved)
    with pytest.raises(reel_thumbs.NotPublicError):
        reel_thumbs._check_public(COVER)


def test_one_private_address_among_public_ones_is_refused(net, tmp_path):
    hosts, _replies, asked = net
    hosts["cdn.example.com"] = [PUBLIC, "10.0.0.5"]

    assert reel_thumbs.kept_thumb(tmp_path / "c.webp", COVER) is None
    assert asked == []


@pytest.mark.parametrize(
    "url", ["https://nowhere.example.com/c.jpg", "file:///etc/passwd", "ftp://x.com/a"]
)
def test_a_host_that_does_not_resolve_or_no_web_link_is_refused(net, tmp_path, url):
    _hosts, _replies, asked = net
    assert reel_thumbs.kept_thumb(tmp_path / "c.webp", url) is None
    assert asked == []


def test_a_redirect_to_a_private_host_is_not_followed(net, tmp_path):
    hosts, replies, asked = net
    hosts["metadata.internal"] = ["169.254.169.254"]
    replies[COVER] = _reply(302, "http://metadata.internal/latest/")

    assert reel_thumbs.kept_thumb(tmp_path / "c.webp", COVER) is None
    assert [url for url, _kw in asked] == [COVER]


def test_a_redirect_to_a_public_host_is_followed(net, tmp_path):
    hosts, replies, asked = net
    hosts["img.example.org"] = ["151.101.1.1"]
    replies[COVER] = _reply(301, "https://img.example.org/c.jpg")
    replies["https://img.example.org/c.jpg"] = _reply()
    dest = tmp_path / "c.webp"

    assert reel_thumbs.kept_thumb(dest, COVER) == dest
    assert [url for url, _kw in asked] == [COVER, "https://img.example.org/c.jpg"]


def test_a_redirect_loop_ends(net, tmp_path):
    _hosts, replies, asked = net
    replies[COVER] = _reply(302, COVER)

    assert reel_thumbs.kept_thumb(tmp_path / "c.webp", COVER) is None
    assert len(asked) == 4


def test_under_fakes_no_address_is_looked_up(net, monkeypatch, tmp_path):
    """OFM_FAKE_EXTERNALS: outbound's fakes answer, nothing leaves."""
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")

    def no_lookup(host: str) -> list[str]:
        raise AssertionError(host)

    monkeypatch.setattr(reel_thumbs, "_addresses", no_lookup)
    dest = tmp_path / "c.webp"

    assert reel_thumbs.kept_thumb(dest, COVER) == dest
