"""`generic.video_info`: /clone-links' look at a link before anything is
downloaded or paid. It reads only the linked post (never the playlist,
profile or story tray around it, never through yt-dlp's Generic extractor),
with a short socket timeout, and keeps the tries' reasons apart: `error` is
the last try's (the login), `logged_out_error` the first proxy's."""

import sys
from types import SimpleNamespace

import pytest

from ofmhelpers.downloaders import generic
from ofmhelpers.downloaders.generic import DownloadConfig

REEL = "https://www.instagram.com/reel/Dejzz26ILDl/"
SHORT = "https://www.youtube.com/shorts/abc"
P1, P2, P3 = "http://u:p@p1:1", "http://u:p@p2:2", "http://u:p@p3:3"
COOKIES = "/app/cookies/cookies.txt"
ENTRY = {
    "id": "Dejzz26ILDl",
    "duration": 7.4,
    "thumbnail": "https://cdn.example/cover.jpg",
    "url": "https://cdn.example/v.mp4",
    "vcodec": "h264",
}


class FakeYtDlp:
    """Records each look's yt-dlp options; `answer(opts)` is its info, or
    an exception it raises."""

    def __init__(self, answer):
        self.seen: list[dict] = []
        outer = self

        class YoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, _url, download):
                assert download is False
                outer.seen.append(self.opts)
                found = answer(self.opts)
                if isinstance(found, Exception):
                    raise found
                return found

        self.YoutubeDL = YoutubeDL


@pytest.fixture(autouse=True)
def real_externals(monkeypatch):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "0")
    monkeypatch.setenv("OFM_IG_PROXIES", f"{P1},{P2},{P3}")
    monkeypatch.setenv("OFM_IG_PROXY_RETRIES", "2")


def _install(monkeypatch, answer) -> FakeYtDlp:
    fake = FakeYtDlp(answer)
    monkeypatch.setitem(
        sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=fake.YoutubeDL)
    )
    return fake


def test_only_the_linked_post_is_read_by_its_sites_extractor(monkeypatch):
    fake = _install(monkeypatch, lambda opts: ENTRY)

    info = generic.video_info(SHORT, COOKIES, socket_timeout=10)

    assert info.seconds == 7.4
    assert info.thumbnail == "https://cdn.example/cover.jpg"
    assert info.error is None
    (opts,) = fake.seen
    assert opts["noplaylist"] is True
    assert opts["playlist_items"] == "1:10"
    assert opts["allowed_extractors"] == ["default", "-generic"]
    assert opts["socket_timeout"] == 10
    assert opts["cookiefile"] == COOKIES


def test_no_socket_timeout_keeps_yt_dlps_own(monkeypatch):
    fake = _install(monkeypatch, lambda opts: ENTRY)

    generic.video_info(SHORT)

    assert "socket_timeout" not in fake.seen[0]


def test_the_entry_limit_is_a_setting(monkeypatch):
    monkeypatch.setenv("OFM_DOWNLOAD_SINGLE_POST_ITEMS", "3")
    fake = _install(monkeypatch, lambda opts: ENTRY)

    generic.video_info(SHORT)

    assert fake.seen[0]["playlist_items"] == "1:3"


def test_a_carousel_gives_its_first_video(monkeypatch):
    photo = {"id": "1", "url": "https://cdn.example/1.jpg", "vcodec": "none"}
    _install(
        monkeypatch,
        lambda opts: {"_type": "playlist", "entries": [photo, ENTRY]},
    )

    assert generic.video_info(SHORT).seconds == 7.4


def test_every_try_failing_reports_the_login_and_keeps_the_first_proxys(
    monkeypatch,
):
    """Proxies rate-limited, then the login refused (expired cookies, 400):
    the 400 is the reason; the proxies' login-page text is kept apart."""

    def answer(opts):
        if "proxy" in opts:
            return RuntimeError(f"redirected to the login page via {opts['proxy']}")
        return RuntimeError("HTTP Error 400: Bad Request")

    fake = _install(monkeypatch, answer)

    info = generic.video_info(REEL, COOKIES)

    assert info.error == "HTTP Error 400: Bad Request"
    first_proxy = fake.seen[0]["proxy"]
    assert info.logged_out_error == f"redirected to the login page via {first_proxy}"
    # 1 + OFM_IG_PROXY_RETRIES proxies logged out, then the login direct.
    assert [("proxy" in o, "cookiefile" in o) for o in fake.seen] == [
        (True, False),
        (True, False),
        (True, False),
        (False, True),
    ]


def test_a_post_hidden_logged_out_skips_to_the_login(monkeypatch):
    def answer(opts):
        if "proxy" in opts:
            return RuntimeError("Instagram sent an empty media response")
        return RuntimeError("HTTP Error 404: Not Found")

    fake = _install(monkeypatch, answer)

    info = generic.video_info(REEL, COOKIES)

    assert len(fake.seen) == 2
    assert info.error == "HTTP Error 404: Not Found"
    assert info.logged_out_error == "Instagram sent an empty media response"


def test_a_site_without_proxies_has_one_try_and_no_logged_out_reason(monkeypatch):
    fake = _install(
        monkeypatch, lambda opts: RuntimeError("Sign in to confirm you're not a bot")
    )

    info = generic.video_info(SHORT, COOKIES)

    assert len(fake.seen) == 1
    assert info.error == "Sign in to confirm you're not a bot"
    assert info.logged_out_error is None


def test_a_proxy_that_reads_it_ends_the_tries(monkeypatch):
    fake = _install(monkeypatch, lambda opts: ENTRY)

    info = generic.video_info(REEL, COOKIES)

    assert info.seconds == 7.4
    (opts,) = fake.seen
    assert opts["proxy"] in {P1, P2, P3}
    assert "cookiefile" not in opts


# --- the real yt-dlp, offline ------------------------------------------------


def _real_ydl(config: DownloadConfig):
    import yt_dlp

    return yt_dlp.YoutubeDL(config.ydl_opts().to_ydl())


def test_yt_dlp_takes_the_one_post_options_and_drops_generic():
    """Pinned yt-dlp accepts `["default", "-generic"]`: every site extractor
    is loaded, Generic is not."""
    with _real_ydl(DownloadConfig.single_post()) as ydl:
        assert "Generic" not in ydl._ies
        assert {"Instagram", "TikTok", "Youtube", "FacebookRedirectURL"} <= set(
            ydl._ies
        )


def test_a_redirector_link_cannot_chain_into_any_host():
    """Review #37: facebook.com/flx/warn/?u=<internal host> passes as a
    Facebook link; with Generic gone yt-dlp refuses the target before any
    request (port 9 would otherwise answer "Connection refused")."""
    import yt_dlp

    link = "https://www.facebook.com/flx/warn/?u=http%3A%2F%2F127.0.0.1%3A9%2F"
    with (
        _real_ydl(DownloadConfig.single_post()) as ydl,
        pytest.raises(yt_dlp.utils.DownloadError, match="No suitable extractor"),
    ):
        ydl.extract_info(link, download=False)


def test_download_media_keeps_every_extractor():
    """/download-media takes any link: its config still has Generic and the
    playlist behaviour."""
    config = DownloadConfig()
    with _real_ydl(config) as ydl:
        assert "Generic" in ydl._ies
    opts = config.ydl_opts().to_ydl()
    assert opts["noplaylist"] is False
    assert "playlist_items" not in opts
    assert "allowed_extractors" not in opts
