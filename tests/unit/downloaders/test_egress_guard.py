import ipaddress

import pytest
import requests

from ofmhelpers.integrations import egress
from ofmhelpers.integrations.downloaders import generic, kvs, media
from ofmhelpers.integrations.downloaders.models import YtDlpInfo

INTERNAL = {
    "postgres": "172.18.0.2",
    "redis": "172.18.0.3",
    "pot-provider": "10.0.0.9",
    "metadata.test": "169.254.169.254",
    "mapped.test": "::ffff:127.0.0.1",
    "nat64.test": "64:ff9b::7f00:1",
    "loopback6.test": "::1",
    "sixtofour.test": "2002:0a00:0001::",
}
PRIVATE_URLS = [
    "http://postgres:5432/",
    "http://redis:6379/",
    "http://pot-provider:4416/ping",
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8000/admin",
    "http://[::1]:8000/",
    "http://metadata.test/",
    "http://mapped.test/",
    "http://nat64.test/",
    "http://loopback6.test/",
    "http://sixtofour.test/",
    "http://user@redis:6379/",
    "file:///etc/passwd",
    "ftp://example.com/a",
    "http://[::1/x",
]


@pytest.fixture
def real_network(monkeypatch):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "0")

    def resolve(host):
        if host in INTERNAL:
            return [INTERNAL[host]]
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return ["151.101.1.1"]
        return [host]

    monkeypatch.setattr(egress, "_addresses", resolve)


@pytest.mark.parametrize("url", PRIVATE_URLS)
def test_a_link_that_is_not_the_public_web_is_refused(real_network, url):
    with pytest.raises(egress.NotPublicError):
        egress.check_public(url)


def test_one_private_address_among_public_ones_is_refused(real_network, monkeypatch):
    monkeypatch.setattr(egress, "_addresses", lambda _h: ["151.101.1.1", "10.0.0.5"])
    with pytest.raises(egress.NotPublicError):
        egress.check_public("https://mixed.example.com/")


def test_a_host_that_does_not_resolve_is_refused(real_network, monkeypatch):
    def fail(_host):
        raise OSError

    monkeypatch.setattr(egress, "_addresses", fail)
    with pytest.raises(egress.NotPublicError):
        egress.check_public("https://nowhere.example.com/")


def test_a_public_host_passes(real_network):
    egress.check_public("https://www.example.com/video")


@pytest.mark.parametrize("url", PRIVATE_URLS[:5])
def test_download_media_runs_no_tool_for_an_internal_link(
    real_network, url, monkeypatch
):
    def never(*_a, **_k):
        msg = "a downloader ran"
        raise AssertionError(msg)

    for module in (media.generic, media.images, media.kvs, media.savers):
        monkeypatch.setattr(module, "download", never)

    result = media.download(url)

    assert not result.success
    assert result.error == "This link is not on the public internet"


@pytest.mark.parametrize("url", PRIVATE_URLS[:5])
def test_the_preview_read_refuses_an_internal_link(real_network, url):
    assert generic.video_info(url).error == "This link is not on the public internet"


def test_the_preview_never_probes_an_internal_stream_named_by_the_page(
    real_network, monkeypatch
):
    page_info = YtDlpInfo(url="http://169.254.169.254/latest/meta-data/", vcodec="h264")
    monkeypatch.setattr(generic._Reading, "try_peek", lambda *_a: page_info)

    def never(*_a, **_k):
        msg = "ffprobe ran on an internal stream"
        raise AssertionError(msg)

    monkeypatch.setattr(generic, "duration_s", never)

    info = generic.video_info("https://www.example.com/post/1")

    assert info.error == "This link is not on the public internet"


class _Reply:
    def __init__(self, location=None):
        self.is_redirect = location is not None
        self.headers = {"location": location} if location else {}
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        return None


def test_a_redirect_to_an_internal_host_is_not_followed_and_is_closed(
    real_network, monkeypatch
):
    reply = _Reply("http://redis:6379/")
    asked = []

    def get(url, **_kwargs):
        asked.append(url)
        return reply

    monkeypatch.setattr(egress.outbound, "get", get)

    with pytest.raises(egress.NotPublicError):
        egress.get("https://example.com/v")

    assert asked == ["https://example.com/v"]
    assert reply.closed


def test_the_fourth_redirect_is_refused(real_network, monkeypatch):
    asked = []

    def get(url, **_kwargs):
        asked.append(url)
        return _Reply("/next")

    monkeypatch.setattr(egress.outbound, "get", get)

    with pytest.raises(requests.TooManyRedirects):
        egress.get("https://example.com/start")

    assert len(asked) == egress.MAX_REDIRECTS + 1


def test_the_kvs_reader_reports_a_redirect_to_an_internal_host(
    real_network, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        egress.outbound, "get", lambda _u, **_k: _Reply("http://redis:6379/")
    )

    result = kvs.download("https://example.com/v", tmp_path)

    assert not result.success
    assert "not on the public internet" in result.error
