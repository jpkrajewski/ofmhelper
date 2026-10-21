"""utils/outbound.py: with OFM_FAKE_EXTERNALS every outside request is
answered by its host's fake, and a host without one is refused -- the app
under e2e can never reach the internet by accident."""

import pytest
import requests

from ofmhelpers.instagram.models import GLOBAL_CDN_HOST
from ofmhelpers.utils import outbound


@pytest.fixture
def fakes_on(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "1")
    monkeypatch.setenv("OFM_FAKES_DIR", str(tmp_path))
    outbound._routes.cache_clear()
    yield
    outbound._routes.cache_clear()


def test_a_host_without_a_fake_is_refused(fakes_on):
    with pytest.raises(requests.ConnectionError, match="no fake for 'example.com'"):
        outbound.get("https://example.com/", timeout=5)


def test_a_mounted_session_answers_from_the_fake(fakes_on):
    session = outbound.mount(requests.Session())
    response = session.get(f"https://{GLOBAL_CDN_HOST}/fake/x.jpg", timeout=5)
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"


def test_streamed_bodies_arrive_whole(fakes_on):
    with outbound.get(
        f"https://{GLOBAL_CDN_HOST}/fake/x.mp4", stream=True, timeout=5
    ) as r:
        body = b"".join(r.iter_content(1024))
    assert body[4:8] == b"ftyp"


def test_head_goes_through_the_fakes_too(fakes_on):
    assert outbound.head(f"https://{GLOBAL_CDN_HOST}/fake/x.jpg", timeout=5).ok


def test_without_the_switch_sessions_are_left_alone(monkeypatch):
    monkeypatch.delenv("OFM_FAKE_EXTERNALS", raising=False)
    session = requests.Session()
    adapter = session.get_adapter("https://example.com/")
    assert outbound.mount(session).get_adapter("https://example.com/") is adapter
