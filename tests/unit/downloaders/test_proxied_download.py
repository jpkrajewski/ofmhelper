"""Instagram refuses the server's IP logged out (login-page redirect) and an
uploaded login that went bad answers 400 (prod, 2026-10-21): every /download
of a reel failed. Instagram URLs now go logged out through the proxies first --
metadata via the proxy, the file from the CDN direct -- and the login + no
proxy try comes last. Other sites keep the single plain try."""

import sys
from types import SimpleNamespace

import pytest

from ofmhelpers.downloaders import generic
from ofmhelpers.downloaders.generic import DownloadConfig

REEL = "https://www.instagram.com/reel/Dejzz26ILDl/"
P1, P2, P3 = "http://u:p@p1:1", "http://u:p@p2:2", "http://u:p@p3:3"
COOKIES = "/app/cookies/cookies.txt"


class FakeYtDlp:
    """Records each YoutubeDL's options and calls; `fails(opts)` decides
    which extractions raise."""

    def __init__(self, out, fails=lambda opts: False):
        self.out = out
        self.fails = fails
        self.calls: list[tuple[str, dict]] = []
        outer = self

        class YoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def extract_info(self, _url, download):
                outer.calls.append(("extract" if download else "info", self.opts))
                if outer.fails(self.opts):
                    raise RuntimeError(
                        "refused " + ("proxied" if "proxy" in self.opts else "direct")
                    )
                return outer._written() if download else {"id": "Dejzz26ILDl"}

            def sanitize_info(self, info):
                return info

            def process_ie_result(self, _info, download):  # noqa: ARG002
                outer.calls.append(("download", self.opts))
                return outer._written()

        self.YoutubeDL = YoutubeDL

    def _written(self):
        self.out.write_bytes(b"mp4")
        return {"requested_downloads": [{"filepath": str(self.out)}]}


@pytest.fixture(autouse=True)
def real_externals(monkeypatch):
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "0")
    monkeypatch.setenv("OFM_IG_PROXIES", f"{P1},{P2},{P3}")
    monkeypatch.setenv("OFM_IG_PROXY_RETRIES", "1")
    monkeypatch.setattr(generic, "get_cookiefile", lambda: COOKIES)


def _install(monkeypatch, tmp_path, fails=lambda opts: False) -> FakeYtDlp:
    fake = FakeYtDlp(tmp_path / "Dejzz26ILDl.mp4", fails)
    monkeypatch.setitem(
        sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=fake.YoutubeDL)
    )
    return fake


def test_a_reel_is_read_through_a_proxy_logged_out_and_fetched_direct(
    monkeypatch, tmp_path
):
    fake = _install(monkeypatch, tmp_path)

    result = generic.download(REEL, DownloadConfig(output_dir=tmp_path))

    assert result.success
    assert result.output_paths == [tmp_path / "Dejzz26ILDl.mp4"]
    (info_kind, info_opts), (dl_kind, dl_opts) = fake.calls
    assert info_kind == "info"
    assert info_opts["proxy"] in {P1, P2, P3}
    assert "cookiefile" not in info_opts
    assert dl_kind == "download"
    assert "proxy" not in dl_opts
    assert "cookiefile" not in dl_opts


def test_failing_proxies_fall_back_to_the_login_direct(monkeypatch, tmp_path):
    fake = _install(monkeypatch, tmp_path, fails=lambda opts: "proxy" in opts)

    result = generic.download(REEL, DownloadConfig(output_dir=tmp_path))

    assert result.success
    proxied = [opts["proxy"] for kind, opts in fake.calls if kind == "info"]
    # 1 + OFM_IG_PROXY_RETRIES proxies, each a different one.
    assert len(proxied) == 2
    assert len(set(proxied)) == 2
    kind, last = fake.calls[-1]
    assert kind == "extract"
    assert last["cookiefile"] == COOKIES
    assert "proxy" not in last


def test_every_try_failing_reports_the_first_reason(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, fails=lambda opts: True)

    result = generic.download(REEL, DownloadConfig(output_dir=tmp_path))

    assert not result.success
    assert result.error == "refused proxied"


def test_other_sites_are_never_proxied(monkeypatch, tmp_path):
    fake = _install(monkeypatch, tmp_path)

    result = generic.download(
        "https://www.youtube.com/shorts/abc", DownloadConfig(output_dir=tmp_path)
    )

    assert result.success
    ((kind, opts),) = fake.calls
    assert kind == "extract"
    assert "proxy" not in opts
    assert opts["cookiefile"] == COOKIES


def test_no_proxies_set_is_the_plain_try(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_IG_PROXIES", "")
    fake = _install(monkeypatch, tmp_path)

    assert generic.download(REEL, DownloadConfig(output_dir=tmp_path)).success
    ((kind, opts),) = fake.calls
    assert kind == "extract"
    assert "proxy" not in opts
