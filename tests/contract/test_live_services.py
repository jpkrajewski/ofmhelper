"""Every outside service the app depends on, reached for real.

Run with `scripts/test.sh live` (Docker tests service), locally -- not on CI
(Instagram rate-limits runner IPs). Each service is checked at two levels: its hosts
resolve and complete a TLS handshake (catches DNS/network breakage, like a
container that cannot resolve anything), and its API answers the way the
app's own client expects (catches a changed or blocked API). Nothing is
paid for and no secret is needed: the paid APIs are called with a key that
must be rejected.
"""

import socket
import ssl
from urllib.parse import urlsplit

import pytest
import yt_dlp
from pydantic import BaseModel

from ofmhelpers.aigenproviders.errors import ProviderAuthError
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.aigenproviders.kaiai.provider import KieProvider
from ofmhelpers.config import settings
from ofmhelpers.instagram import InstagramRateLimitedError, ProfileNotFoundError
from ofmhelpers.instagram import client as instagram_client
from ofmhelpers.instagram.client import InstagramClient
from ofmhelpers.instagram.models import GLOBAL_CDN_HOST

pytestmark = pytest.mark.live

TLS_PORT = 443
TIMEOUT_S = 15
# A public TikTok video kept up for years (yt-dlp's own TikTok test case).
TIKTOK_VIDEO = "https://www.tiktok.com/@scout2015/video/6718335390845095173"
TIKTOK_HOST = "www.tiktok.com"
# A public Instagram profile that will not disappear.
INSTAGRAM_PROFILE = "instagram"
BAD_KEY = "integration-test-invalid-key"


class Host(BaseModel):
    service: str
    name: str

    def __str__(self) -> str:
        return f"{self.service}:{self.name}"


def _hosts() -> list[Host]:
    """Every host the app talks to, read from its own config/constants."""
    urls = {
        "tiktok": [f"https://{TIKTOK_HOST}"],
        "instagram": [instagram_client.ROOT, f"https://{GLOBAL_CDN_HOST}"],
        "kie": [settings.kieai.jobs_base, settings.kieai.upload_base],
        "higgsfield": [settings.higgsfield.base_url],
    }
    return [
        Host(service=service, name=urlsplit(url).hostname or url)
        for service, service_urls in urls.items()
        for url in service_urls
    ]


@pytest.mark.parametrize("host", _hosts(), ids=str)
def test_host_resolves_and_completes_tls(host: Host):
    addresses = socket.getaddrinfo(host.name, TLS_PORT, type=socket.SOCK_STREAM)
    assert addresses, f"{host} did not resolve"
    context = ssl.create_default_context()
    with (
        socket.create_connection((host.name, TLS_PORT), timeout=TIMEOUT_S) as raw,
        context.wrap_socket(raw, server_hostname=host.name) as tls,
    ):
        assert tls.version()


def test_instagram_serves_a_public_profile():
    """Like the app's scans: direct first, then each configured proxy
    (`OFM_IG_PROXIES`). Like the app, a rate limit or a "not found" for an
    account that exists means a flagged IP: try the next route (prod's own
    IP gets "not found", 2026-10-20)."""
    routes = [None, *settings.instagram.proxy_urls]
    for proxy in routes:
        try:
            profile = InstagramClient(proxy).open_profile(INSTAGRAM_PROFILE)
        except (InstagramRateLimitedError, ProfileNotFoundError):
            continue
        assert profile.followers > 0
        return
    pytest.fail(f"Instagram refused all {len(routes)} routes")


def test_tiktok_video_metadata_extracts():
    options = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(TIKTOK_VIDEO, download=False)

    assert info["id"] == TIKTOK_VIDEO.rsplit("/", 1)[1]
    assert info.get("formats")


def test_kie_answers_and_rejects_a_bad_key():
    with pytest.raises(ProviderAuthError):
        KieProvider(BAD_KEY).status("integration-test")


def test_higgsfield_answers_and_rejects_a_bad_key():
    with pytest.raises(ProviderAuthError):
        HiggsfieldProvider(f"{BAD_KEY}:{BAD_KEY}").status(
            "00000000-0000-0000-0000-000000000000"
        )
