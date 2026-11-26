# Copyright (C) 2026 jpkrajewski

import io
from pathlib import Path
from types import MappingProxyType

import pytest
from starlette.datastructures import UploadFile

from ofmhelpers.features.assets import manifests, store
from ofmhelpers.features.files import disk
from ofmhelpers.features.refusals import (
    AssetRefusedError,
    ConflictError,
    RefusedError,
)
from ofmhelpers.features.signin.state import HOME, safe_next
from ofmhelpers.integrations.downloaders.images import ImageDownloadConfig
from ofmhelpers.integrations.pinterest import only_pinterest
from ofmhelpers.media import video_probe

NUL = chr(0)


@pytest.mark.parametrize(
    "next_url",
    ["/\\evil.com", "/a\\b", "/\t/evil.com", "/\n/evil", "/a" + chr(1), "//evil.com"],
)
def test_safe_next_refuses_a_backslash_or_a_control_character(next_url):
    assert safe_next(next_url) == HOME


def test_safe_next_keeps_a_path_on_this_site():
    assert safe_next("/clone?x=1") == "/clone?x=1"


def test_a_nul_in_an_upload_name_is_a_refusal():
    with pytest.raises(AssetRefusedError):
        store.safe_filename("clip" + NUL + ".mp4")


def test_a_nul_in_a_reference_path_is_a_refusal(tmp_path):
    with pytest.raises(AssetRefusedError):
        manifests.resolve_existing_ref("a" + NUL + "b", tmp_path)


def test_a_nul_in_a_disk_path_is_a_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(
        disk,
        "_roots",
        lambda: MappingProxyType({"uploads": tmp_path.resolve()}),
    )
    with pytest.raises(RefusedError):
        disk.safe_path("uploads", "a" + NUL + "b")


def _upload(name: str, body: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(body), filename=name)


def test_a_disk_upload_is_refused_over_a_file_and_keeps_it(tmp_path, monkeypatch):
    monkeypatch.setattr(
        disk,
        "_roots",
        lambda: MappingProxyType({"uploads": tmp_path.resolve()}),
    )
    (tmp_path / "a.txt").write_bytes(b"first")

    with pytest.raises(ConflictError, match="a.txt already exists"):
        disk.upload("uploads", "", [_upload("a.txt", b"second")])

    assert (tmp_path / "a.txt").read_bytes() == b"first"


def test_a_disk_upload_is_refused_over_a_dangling_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(
        disk,
        "_roots",
        lambda: MappingProxyType({"uploads": tmp_path.resolve()}),
    )
    try:
        (tmp_path / "a.txt").symlink_to(tmp_path / "nowhere.txt")
    except OSError:
        pytest.skip("symlinks need a privilege on this host")

    with pytest.raises(ConflictError):
        disk.upload("uploads", "", [_upload("a.txt", b"x")])

    assert not (tmp_path / "nowhere.txt").exists()


def test_a_disk_upload_writes_a_new_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        disk,
        "_roots",
        lambda: MappingProxyType({"uploads": tmp_path.resolve()}),
    )
    disk.upload("uploads", "", [_upload("a.txt", b"hello")])
    assert (tmp_path / "a.txt").read_bytes() == b"hello"


def test_a_failed_save_asset_removes_its_temp_file(tmp_path, monkeypatch):
    def refuse(self, name):
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(Path, "rename", refuse)

    with pytest.raises(OSError, match="disk full"):
        store.save_asset(_upload("a.png", b"png-bytes"), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_a_save_asset_that_cannot_read_removes_its_temp_file(tmp_path):
    class Broken(io.BytesIO):
        def read(self, *_):
            msg = "client went away"
            raise OSError(msg)

    with pytest.raises(OSError, match="client went away"):
        store.save_asset(UploadFile(file=Broken(), filename="a.png"), tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_save_asset_keeps_the_stored_file(tmp_path):
    stored = Path(store.save_asset(_upload("a.png", b"png-bytes"), tmp_path))
    assert stored.read_bytes() == b"png-bytes"
    assert [p.name for p in tmp_path.iterdir()] == [stored.name]


def test_gallery_dl_gets_the_url_after_a_double_dash():
    cmd = ImageDownloadConfig().gallery_dl_cmd("--config=/etc/x", "cookies.txt")
    assert cmd[-2:] == ["--", "--config=/etc/x"]


@pytest.fixture
def probed(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, timeout_s):
        calls.append(args)
        return "12.5\n"

    monkeypatch.setattr(video_probe, "run", fake_run)
    monkeypatch.setattr(video_probe, "binary", lambda name: name)
    return calls


def test_ffprobe_reads_a_path_after_dash_i(probed):
    assert video_probe.duration_s(Path("clip.mp4"), 5) == 12.5
    assert probed[0][-2:] == ["-i", "clip.mp4"]


def test_ffprobe_reads_an_http_url(probed):
    video_probe.duration_s("https://cdn.example/clip.mp4", 5)
    assert probed[0][-2:] == ["-i", "https://cdn.example/clip.mp4"]


@pytest.mark.parametrize(
    "src",
    ["/etc/passwd", "file:///etc/passwd", "concat:a|b", "-f", "clip.mp4"],
)
def test_ffprobe_refuses_a_str_that_is_not_an_http_url(probed, src):
    with pytest.raises(ValueError, match="http"):
        video_probe.duration_s(src, 5)
    with pytest.raises(ValueError, match="http"):
        video_probe.frame_rate(src, 5)
    assert probed == []


def _txt(*domains: str) -> str:
    rows = ["# Netscape HTTP Cookie File"]
    for i, domain in enumerate(domains):
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        rows.append(f"{domain}\t{flag}\t/\tTRUE\t1999999999\tn{i}\tv{i}")
    return "\n".join(rows) + "\n"


def test_only_pinterest_keeps_the_domain_and_its_subdomains_only():
    kept = only_pinterest(
        _txt(
            ".pinterest.com",
            "pinterest.com",
            "pl.pinterest.com",
            "evilpinterest.com",
            ".notpinterest.com",
            "pinterest.com.evil.test",
        ),
    )

    names = {line.split("\t")[5] for line in kept.splitlines() if "\t" in line}
    assert names == {"n0", "n1", "n2"}
