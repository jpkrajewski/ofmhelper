"""The saver-site fallback (downloaders/savers.py) on the wire fakes: the
real client runs against saveinta.com / snapreels.net / dl.snapcdn.app
answered in-process the way the live sites answer (savers_fake.py)."""

import json
from pathlib import Path

import pytest

from ofmhelpers.downloaders import savers
from ofmhelpers.downloaders.sites import saver_supported

_PACKED = (
    Path(__file__).parents[2] / "fixtures" / "downloads" / "saveinta_ajax_packed.json"
)
REEL = "https://www.instagram.com/reel/Dc-i7hPgI7_/"
POST = "https://www.instagram.com/p/DeeTMScxZOL/"
CAROUSEL = "https://www.instagram.com/p/fake-carousel/"


@pytest.fixture(autouse=True)
def _fake_externals(fake_externals):
    """Every test here runs on the wire fakes (nothing reaches the net)."""


def test_a_reel_comes_back_as_its_mp4(tmp_path):
    result = savers.download(REEL, tmp_path)

    assert result.success
    (path,) = result.output_paths
    assert path.name == "Dc-i7hPgI7__1.mp4"
    assert path.stat().st_size > 0


def test_a_photo_post_comes_back_as_its_images(tmp_path):
    result = savers.download(POST, tmp_path)

    assert result.success
    assert [p.name for p in result.output_paths] == [
        "DeeTMScxZOL_1.jpg",
        "DeeTMScxZOL_2.jpg",
    ]


def test_the_second_saver_picks_up_when_the_first_fails(monkeypatch, tmp_path):
    """saveinta is down: snapreels answers instead."""
    real = savers._fetch

    def no_saveinta(url, base, out, limit):
        if "saveinta" in base:
            down = "saveinta is down"
            raise ConnectionError(down)
        return real(url, base, out, limit)

    monkeypatch.setattr(savers, "_fetch", no_saveinta)

    result = savers.download(REEL, tmp_path)

    assert result.success
    assert len(result.output_paths) == 1


def test_a_removed_post_fails_with_both_reasons(tmp_path):
    result = savers.download(f"{REEL}fake-fail/", tmp_path)

    assert not result.success
    assert "saveinta:" in result.error
    assert "snapreels:" in result.error


def test_a_non_instagram_link_is_refused_without_any_request(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        savers.outbound, "get", lambda *a, **k: calls.append(a) or pytest.fail("GET")
    )

    result = savers.download("https://www.tiktok.com/@a/video/1", tmp_path)

    assert not result.success
    assert "Unsupported URL" in result.error
    assert calls == []


def test_single_limits_a_carousel_to_the_one_post_items(tmp_path, monkeypatch):
    monkeypatch.setenv("OFM_DOWNLOAD_SINGLE_POST_ITEMS", "3")

    result = savers.download(CAROUSEL, tmp_path, single=True)

    assert result.success
    assert len(result.output_paths) == 3


def test_the_download_tool_keeps_every_slide(tmp_path):
    result = savers.download(CAROUSEL, tmp_path)

    assert result.success
    assert len(result.output_paths) == 12


def test_a_web_page_instead_of_media_is_not_a_file(monkeypatch, tmp_path):
    """A saver whose file link answers HTML (an ad, an expired token page)
    gives no files, not a saved web page."""

    class _HtmlAnswer:
        headers = {"content-type": "text/html; charset=utf-8"}

        def raise_for_status(self):
            return None

        def iter_content(self, _n):
            yield b"<html>not media</html>"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    real_get = savers.outbound.get

    def get(url, **kwargs):
        if "dl.snapcdn.app" in url:
            return _HtmlAnswer()
        return real_get(url, **kwargs)

    monkeypatch.setattr(savers.outbound, "get", get)

    result = savers.download(REEL, tmp_path)
    assert not result.success
    assert list(tmp_path.rglob("*")) == []


def test_only_saver_links_are_taken_from_the_result():
    """Ads (play-store buttons, home links) share the result page with the
    media links; only dl.snapcdn.app is a file."""
    html = (
        '<a href="https://play.google.com/store/apps/details?id=com.saveinsta">'
        '<a href="/en"><a href="https://dl.snapcdn.app/get?token=a.mp4">'
    )
    assert savers._links(html) == ["https://dl.snapcdn.app/get?token=a.mp4"]


def test_the_packed_result_of_the_live_shape_decodes():
    """A response captured from the live saveinta (2026-10-29): the packed
    script must decode to the download list."""
    captured = json.loads(_PACKED.read_text(encoding="utf-8"))

    links = savers._links(captured["data"])

    assert len(links) == 1
    assert links[0].startswith("https://dl.snapcdn.app/saveinsta?token=")


def test_unpack_leaves_plain_html_alone():
    assert savers._unpack('<a href="https://dl.snapcdn.app/get?token=x">') == (
        '<a href="https://dl.snapcdn.app/get?token=x">'
    )


@pytest.mark.parametrize(
    "url",
    [
        REEL,
        "https://www.instagram.com/reels/abc/",
        "https://www.instagram.com/p/abc/",
        "https://www.instagram.com/someone/p/abc/",
        "https://www.instagram.com/tv/abc/",
        "https://instagram.com/reel/abc/",
    ],
)
def test_saver_supported_posts(url):
    assert saver_supported(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/fitpetyo/",
        "https://www.instagram.com/stories/someone/",
        "https://www.tiktok.com/@a/video/1",
        "https://x.com/user/status/1",
        "not a url at all",
    ],
)
def test_saver_supported_refuses_the_rest(url):
    assert not saver_supported(url)
