"""One Download tool for any link: each URL tries yt-dlp and gallery-dl in
the order its kind of post suggests, and keeps the first that gets files."""

import subprocess
from pathlib import Path

import pytest

from ofmhelpers.downloaders import generic, images, kvs, media
from ofmhelpers.downloaders.models import DownloadResult


@pytest.fixture(autouse=True)
def _no_kvs_site(monkeypatch):
    """The KVS reader finds no player (and never reaches the network)
    unless a test says otherwise."""
    monkeypatch.setattr(
        kvs,
        "download",
        lambda url, output_dir=None: DownloadResult(
            url=url, success=False, error="Unsupported URL: no KVS player"
        ),
    )


@pytest.fixture
def tried(monkeypatch) -> list[str]:
    """Which downloader ran, in order; each fails unless the URL names it."""
    calls: list[str] = []

    def fake(name: str):
        def run(url: str, config=None, cookiefile=None) -> DownloadResult:
            calls.append(name)
            ok = name in url
            return DownloadResult(
                url=url,
                success=ok,
                output_paths=[f"/x/{name}.bin"] if ok else [],
                error=None if ok else f"{name} refused",
            )

        return run

    monkeypatch.setattr(generic, "download", fake("video"))
    monkeypatch.setattr(images, "download", fake("images"))
    kvs_run = fake("kvs")
    monkeypatch.setattr(kvs, "download", lambda url, output_dir=None: kvs_run(url))
    return calls


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@a/video/1",
        "https://www.instagram.com/reel/abc/",
        "https://www.youtube.com/shorts/abc",
    ],
)
def test_video_links_try_yt_dlp_first(tried, url):
    media.download(url)
    assert tried[0] == "video"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/abc/",
        "https://www.instagram.com/someone/p/abc/",
        "https://x.com/user/status/1",
        "https://twitter.com/user/status/1",
        "https://www.pinterest.com/pin/1/",
        "https://pin.it/abc",
        "https://www.tiktok.com/@a/photo/1",
    ],
)
def test_photo_post_links_try_gallery_dl_first(tried, url):
    media.download(url)
    assert tried[0] == "images"


def test_a_video_link_that_is_a_photo_post_falls_back_to_gallery_dl(tried):
    result = media.download("https://www.tiktok.com/@a/1234?images")

    assert result.success
    assert tried == ["video", "kvs", "images"]
    assert [str(p) for p in result.output_paths] == ["/x/images.bin"]


def test_the_first_success_wins_and_the_other_is_not_run(tried):
    assert media.download("https://www.instagram.com/p/abc/images").success
    assert tried == ["images"]


def test_when_both_fail_both_reasons_are_kept(tried):
    result = media.download("https://example.com/nothing")

    assert not result.success
    assert result.error == (
        "video: video refused | kvs: kvs refused | images: images refused"
    )


def test_a_tool_that_does_not_know_the_site_does_not_hide_the_real_reason(
    monkeypatch,
):
    """Prod, 2026-10-22: PornHub's 403 was shown as "Unsupported URL"."""

    def fails(error):
        return lambda url, config=None, cookiefile=None: DownloadResult(
            url=url, success=False, error=error
        )

    monkeypatch.setattr(media.generic, "download", fails("HTTP Error 403: Forbidden"))
    monkeypatch.setattr(
        media.images, "download", fails("[gallery-dl][error] Unsupported URL 'x'")
    )
    assert (
        media.download("https://x.test/v").error == "video: HTTP Error 403: Forbidden"
    )

    monkeypatch.setattr(media.generic, "download", fails("ERROR: Unsupported URL: y"))
    both = media.download("https://x.test/v").error
    assert "video: ERROR: Unsupported URL" in both
    assert "images:" in both


def test_download_all_keeps_the_order(tried):
    urls = ["https://a.example/video", "https://www.instagram.com/p/images"]
    assert [r.url for r in media.download_all(urls)] == urls


def test_download_all_logs_in_with_the_given_cookies_then_deletes_them(monkeypatch):
    """Both downloaders get one file holding exactly the user's cookies.txt;
    it is gone once the run ends."""
    seen: list[tuple[str, str]] = []

    def fail(url: str, config=None, cookiefile=None) -> DownloadResult:
        assert cookiefile is not None
        seen.append((cookiefile, Path(cookiefile).read_text(encoding="utf-8")))
        return DownloadResult(url=url, success=False, error="no")

    monkeypatch.setattr(generic, "download", fail)
    monkeypatch.setattr(images, "download", fail)
    jar = "# Netscape HTTP Cookie File\n"

    media.download_all(["https://example.com/a"], cookies=jar)

    assert [text for _path, text in seen] == [jar, jar]
    assert len({path for path, _text in seen}) == 1
    assert not Path(seen[0][0]).exists()


def test_download_all_without_cookies_runs_logged_out(monkeypatch):
    logins: list[str | None] = []

    def run(url: str, config=None, cookiefile=None) -> DownloadResult:
        logins.append(cookiefile)
        return DownloadResult(url=url, success=True, output_paths=["/x/v.bin"])

    monkeypatch.setattr(generic, "download", run)
    media.download_all(["https://example.com/a"])
    assert logins == [None]


# --- a "video" that is not one (2026-10-23) ----------------------------------


def _real_video(path: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x36:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def test_a_web_page_saved_as_mp4_is_no_download_and_the_next_tool_runs(
    monkeypatch, tmp_path
):
    """yt-dlp saved gayhub's HTML as a 268 KB .mp4 and called it done: now
    it is deleted, and the KVS reader's real video is what comes back."""
    page = tmp_path / "NA_clip-1NA.mp4"
    page.write_text("<!DOCTYPE html><html>a web page</html>")
    video = _real_video(tmp_path / "gayhub_168792.mp4")
    monkeypatch.setattr(
        generic,
        "download",
        lambda url, config=None, cookiefile=None: DownloadResult(
            url=url, success=True, output_paths=[page]
        ),
    )
    monkeypatch.setattr(
        kvs,
        "download",
        lambda url, output_dir=None: DownloadResult(
            url=url, success=True, output_paths=[video]
        ),
    )

    result = media.download("https://www.gayhub.com/video/168792/clip/")

    assert result.success
    assert result.output_paths == [video]
    assert not page.exists()


def test_when_every_tool_gets_a_web_page_it_fails_saying_so(monkeypatch, tmp_path):
    def page_as_video(url, config=None, cookiefile=None):
        path = tmp_path / "fake.mp4"
        path.write_text("<html>not a video</html>")
        return DownloadResult(url=url, success=True, output_paths=[path])

    monkeypatch.setattr(generic, "download", page_as_video)
    monkeypatch.setattr(
        images,
        "download",
        lambda url, config=None, cookiefile=None: DownloadResult(
            url=url, success=False, error="no"
        ),
    )

    result = media.download("https://example.com/v")

    assert not result.success
    assert "video: the site sent a web page, not the video" in result.error
    assert not (tmp_path / "fake.mp4").exists()


def test_a_real_video_passes_the_check(monkeypatch, tmp_path):
    video = _real_video(tmp_path / "v.mp4")
    monkeypatch.setattr(
        generic,
        "download",
        lambda url, config=None, cookiefile=None: DownloadResult(
            url=url, success=True, output_paths=[video]
        ),
    )
    assert media.download("https://example.com/v").output_paths == [video]
    assert video.exists()


# --- one post only (/clone-links, review #12) --------------------------------


@pytest.fixture
def configs(monkeypatch) -> dict[str, object]:
    """The config each tool got; both fail, so both run."""
    seen: dict[str, object] = {}

    def keep(name: str):
        def run(url: str, config=None, cookiefile=None) -> DownloadResult:
            seen[name] = config
            return DownloadResult(url=url, success=False, error=f"{name} refused")

        return run

    monkeypatch.setattr(generic, "download", keep("video"))
    monkeypatch.setattr(images, "download", keep("images"))
    return seen


def test_a_single_download_asks_both_tools_for_the_linked_post_only(
    configs, monkeypatch, tmp_path
):
    monkeypatch.setattr(media, "_OUTPUT_ROOT", tmp_path)

    with media.downloaded("https://www.instagram.com/p/abc/", single=True):
        pass

    video, pictures = configs["video"], configs["images"]
    assert isinstance(video, generic.DownloadConfig)
    opts = video.ydl_opts().to_ydl()
    assert opts["noplaylist"] is True
    assert opts["playlist_items"] == "1:10"
    assert opts["allowed_extractors"] == ["default", "-generic"]
    assert isinstance(pictures, images.ImageDownloadConfig)
    cmd = pictures.gallery_dl_cmd("https://www.instagram.com/p/abc/")
    assert cmd[cmd.index("--range") + 1] == "1-10"


def test_the_download_tool_keeps_playlists_and_every_extractor(configs):
    media.download("https://www.instagram.com/p/abc/")

    video, pictures = configs["video"], configs["images"]
    assert isinstance(video, generic.DownloadConfig)
    opts = video.ydl_opts().to_ydl()
    assert opts["noplaylist"] is False
    assert "playlist_items" not in opts
    assert "allowed_extractors" not in opts
    assert isinstance(pictures, images.ImageDownloadConfig)
    assert "--range" not in pictures.gallery_dl_cmd("https://www.instagram.com/p/abc/")


# --- downloaded(): the URL's directory is gone after the block (review #45) --


def _writes(name: str, *, ok: bool):
    """A tool that writes into its directory (a partial file when it
    fails) and reports it."""

    def run(url: str, config=None, cookiefile=None) -> DownloadResult:
        out = config.output_dir
        out.mkdir(parents=True, exist_ok=True)
        path = out / (f"{name}.jpg" if ok else f"{name}.mp4.part")
        path.write_bytes(b"x")
        if ok:
            return DownloadResult(url=url, success=True, output_paths=[path])
        return DownloadResult(url=url, success=False, error=f"{name} refused")

    return run


def test_downloaded_files_exist_in_the_block_and_their_folder_goes_after(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(media, "_OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(images, "download", _writes("images", ok=True))

    with media.downloaded("https://www.instagram.com/p/abc/") as result:
        assert result.success
        (path,) = result.output_paths
        assert path.read_bytes() == b"x"
        assert path.parent.parent == tmp_path

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_leaves_no_folder_or_partial_file(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "_OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(generic, "download", _writes("video", ok=False))
    monkeypatch.setattr(images, "download", _writes("images", ok=False))

    with media.downloaded("https://www.tiktok.com/@a/video/1", single=True) as result:
        assert not result.success
        (folder,) = tmp_path.iterdir()
        assert sorted(p.name for p in folder.iterdir()) == [
            "images.mp4.part",
            "video.mp4.part",
        ]

    assert list(tmp_path.iterdir()) == []


class _StoreFullError(RuntimeError):
    """The caller's own work inside the block failed."""


def _keep_then_fail(url: str) -> None:
    with media.downloaded(url) as result:
        assert result.output_paths[0].exists()
        raise _StoreFullError


def test_the_folder_goes_when_the_block_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "_OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(images, "download", _writes("images", ok=True))

    with pytest.raises(_StoreFullError):
        _keep_then_fail("https://www.instagram.com/p/abc/")

    assert list(tmp_path.iterdir()) == []


def test_each_downloaded_url_gets_its_own_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(media, "_OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(images, "download", _writes("images", ok=True))

    with (
        media.downloaded("https://www.instagram.com/p/a/") as first,
        media.downloaded("https://www.instagram.com/p/b/") as second,
    ):
        assert first.output_paths[0].parent != second.output_paths[0].parent
        assert len(list(tmp_path.iterdir())) == 2

    assert list(tmp_path.iterdir()) == []
