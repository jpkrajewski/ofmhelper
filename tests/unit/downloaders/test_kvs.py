from pathlib import Path

import pytest
import requests

from ofmhelpers.integrations.downloaders import kvs
from ofmhelpers.integrations.downloaders.kvs import KvsPage

PAGE = Path(__file__).parents[2] / "fixtures" / "downloads" / "kvs_page.html"
URL = "https://www.gayhub.com/video/168792/clip/"
REAL_1080P = (
    "https://www.gayhub.com/get_file/1/23f0108764a6db818572bdae4b6fe10887cfd94aca"
    "/168000/168792/168792_1080p.mp4/"
)


def test_a_kvs_page_lists_every_quality():
    page = KvsPage.parse(PAGE.read_text())

    assert page is not None
    assert page.video_id == "168792"
    assert page.license_code == "$426154614059587"
    assert sorted(s.height for s in page.sources) == [480, 720, 1080]


def test_the_best_quality_is_unscrambled_with_the_license_code():
    page = KvsPage.parse(PAGE.read_text())
    assert page is not None
    assert page.best() == REAL_1080P


@pytest.mark.parametrize(
    "html",
    [
        "<html>an ordinary page</html>",
        "<script>var flashvars = {video_id: '1'};</script>",
        "<script>var flashvars = {license_code: '$1'};</script>",
    ],
)
def test_a_page_without_a_kvs_player_is_not_one(html):
    assert KvsPage.parse(html) is None


class _Reply:
    is_redirect = False

    def close(self):
        return None

    def __init__(self, text="", content=b"", status=200):
        self.text, self._content, self.status_code = text, content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            msg = f"{self.status_code}"
            raise requests.HTTPError(msg)

    def iter_content(self, _size):
        yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_download_saves_the_best_quality_with_the_page_as_referer(
    monkeypatch, tmp_path
):
    asked = []

    def get(url, headers=None, timeout=None, stream=False, allow_redirects=True):
        asked.append((url, headers.get("Referer")))
        if url == URL:
            return _Reply(text=PAGE.read_text())
        return _Reply(content=b"\x00\x00\x00 ftypisom video bytes")

    monkeypatch.setattr(kvs.egress.outbound, "get", get)

    result = kvs.download(URL, tmp_path)

    assert result.success, result.error
    [saved] = result.output_paths
    assert saved == tmp_path / "gayhub_168792.mp4"
    assert saved.read_bytes() == b"\x00\x00\x00 ftypisom video bytes"
    assert asked[1] == (REAL_1080P, URL)
    assert list(tmp_path.glob("*.part")) == []


def test_a_failed_video_stream_leaves_no_file(monkeypatch, tmp_path):
    def get(url, headers=None, timeout=None, stream=False, allow_redirects=True):
        if url == URL:
            return _Reply(text=PAGE.read_text())
        return _Reply(status=403)

    monkeypatch.setattr(kvs.egress.outbound, "get", get)

    result = kvs.download(URL, tmp_path)

    assert not result.success
    assert "KVS video: 403" in result.error
    assert list(tmp_path.iterdir()) == []


def test_a_site_without_a_kvs_player_says_unsupported(monkeypatch, tmp_path):
    monkeypatch.setattr(
        kvs.egress.outbound, "get", lambda *a, **k: _Reply(text="<html>plain</html>")
    )
    result = kvs.download("https://example.com/v", tmp_path)
    assert (result.success, result.error) == (False, "Unsupported URL: no KVS player")
