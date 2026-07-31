"""
Covers /refs/thumb: the cached small-preview endpoint the reuse-picker grid
and job-status "Inputs" section use instead of downloading full-res
originals (see task_helpers.reference_asset, static/js/file-picker.js).
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ofmhelpers.web.main import app
from ofmhelpers.web.routers import refs as refs_router


def _png_bytes(size=(300, 300)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color="blue").save(buf, format="PNG")
    return buf.getvalue()


def _write_test_video(dest: Path, width: int = 640, height: int = 360) -> None:
    """A real (tiny) mp4 -- ffmpeg has to actually be able to decode a frame,
    so a fake byte blob won't do here."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:duration=1:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    d = tmp_path / "assets"
    d.mkdir()
    monkeypatch.setattr(refs_router, "ASSETS_ROOT", d)
    monkeypatch.setattr(refs_router, "THUMBS_DIR", d / ".thumbs")
    return d


def test_thumb_is_smaller_than_the_original(client, assets_dir):
    original = assets_dir / "abc123__photo.png"
    original.write_bytes(_png_bytes())

    r = client.get(f"/refs/thumb?path={original}&size=100")

    assert r.status_code == 200
    assert len(r.content) < original.stat().st_size


def test_thumb_is_cached_on_disk_and_reused(client, assets_dir):
    original = assets_dir / "abc123__photo.png"
    original.write_bytes(_png_bytes())

    r1 = client.get(f"/refs/thumb?path={original}&size=100")
    cached = list((assets_dir / ".thumbs").glob("*"))
    assert len(cached) == 1

    r2 = client.get(f"/refs/thumb?path={original}&size=100")
    assert r2.content == r1.content


def test_thumb_rejects_path_outside_assets_root(client, tmp_path, assets_dir):
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes())

    r = client.get(f"/refs/thumb?path={outside}")
    assert r.status_code == 403


def test_thumb_404s_for_audio(client, assets_dir):
    """Audio has no frame to show -- the picker uses an icon tile for it."""
    audio = assets_dir / "abc123__voice.mp3"
    audio.write_bytes(b"not a real mp3, doesn't matter here")

    r = client.get(f"/refs/thumb?path={audio}")
    assert r.status_code == 404


def test_thumb_404s_for_unreadable_video_instead_of_500(client, assets_dir):
    """A corrupt/truncated clip must not take down the whole picker grid --
    the client falls back to its icon tile on a 404."""
    video = assets_dir / "abc123__clip.mp4"
    video.write_bytes(b"definitely not a video container")

    r = client.get(f"/refs/thumb?path={video}")
    assert r.status_code == 404


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_thumb_is_a_cached_webp_frame(client, assets_dir):
    """Video tiles poster through a first-frame grab rather than making the
    browser range-request the multi-MB original."""
    video = assets_dir / "abc123__clip.mp4"
    _write_test_video(video)

    r = client.get(f"/refs/thumb?path={video}&size=200")

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert len(r.content) < video.stat().st_size
    # cached on disk, keyed by the same content hash as image thumbs
    assert (assets_dir / ".thumbs" / "abc123__200.webp").is_file()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_thumb_preserves_aspect_ratio(client, assets_dir):
    """A 9:16 reel must not come back stretched into a square."""
    video = assets_dir / "def456__portrait.mp4"
    _write_test_video(video, width=360, height=640)

    r = client.get(f"/refs/thumb?path={video}&size=200")

    assert r.status_code == 200
    with Image.open(io.BytesIO(r.content)) as img:
        assert img.width < img.height
        assert max(img.size) <= 200


def test_thumb_404s_for_missing_file(client, assets_dir):
    missing = assets_dir / "abc123__gone.png"
    r = client.get(f"/refs/thumb?path={missing}")
    assert r.status_code == 404


def test_list_refs_still_returns_full_res_file_path(client, assets_dir):
    """/refs itself is unchanged -- the client still gets the real path, and
    still uses /refs/file for the actual (full-res) selected asset."""
    original = assets_dir / "abc123__photo.png"
    original.write_bytes(_png_bytes())

    r = client.get("/refs?kind=image")
    assert r.status_code == 200
    [entry] = r.json()
    assert entry["path"] == str(original)

    file_r = client.get(f"/refs/file?path={entry['path']}")
    assert file_r.content == original.read_bytes()


def _write_refs(assets_dir: Path, count: int, ext: str = ".png") -> list[str]:
    """`count` assets with strictly increasing mtimes, oldest first."""
    names = []
    for i in range(count):
        path = assets_dir / f"hash{i:03d}__file{i}{ext}"
        path.write_bytes(_png_bytes(size=(8, 8)))
        os.utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
        names.append(path.name)
    return names


def test_list_refs_opens_on_last_used_then_last_uploaded(
    client, assets_dir, monkeypatch
):
    """The picker opens on the handful you actually just worked with, then the
    handful you last added -- a wall of tiles buried the file you wanted.

    The two lists are different orderings on purpose: mtime is "uploaded" and
    nothing rewrites it, so a file you use daily can't sink out of view and a
    file you just uploaded can't be hidden by one you used once."""
    names = _write_refs(assets_dir, 12)
    used = [str(assets_dir / names[0]), str(assets_dir / names[3])]
    monkeypatch.setattr(
        refs_router.ref_usage,
        "recent",
        lambda _limit: [(used[0], 20.0), (used[1], 10.0)],
    )

    entries = client.get("/refs?kind=image").json()

    # used first (newest use first), then the newest uploads that aren't in it
    assert [e["name"] for e in entries] == [
        "file0.png",
        "file3.png",
        "file11.png",
        "file10.png",
        "file9.png",
        "file8.png",
        "file7.png",
    ]
    assert [e["used_at"] for e in entries[:2]] == [20.0, 10.0]
    assert all(e["used_at"] is None for e in entries[2:])


def test_list_refs_ignores_used_files_that_are_gone_or_another_kind(
    client, assets_dir, monkeypatch
):
    """A used file can be deleted through the file manager, and one picker's
    usage record is another picker's wrong kind."""
    _write_refs(assets_dir, 3)
    (assets_dir / "hashaud__voice.mp3").write_bytes(b"id3")
    monkeypatch.setattr(
        refs_router.ref_usage,
        "recent",
        lambda _limit: [
            (str(assets_dir / "gone.png"), 30.0),
            (str(assets_dir / "hashaud__voice.mp3"), 20.0),
        ],
    )

    entries = client.get("/refs?kind=image").json()

    assert all(e["used_at"] is None for e in entries)
    assert [e["name"] for e in entries] == ["file2.png", "file1.png", "file0.png"]


def test_list_refs_limit_reaches_the_older_ones(client, assets_dir):
    """What the picker's "Show older" button asks for: newest-uploaded first,
    no usage grouping."""
    _write_refs(assets_dir, 12)

    entries = client.get(f"/refs?kind=image&limit={refs_router.MAX_REF_LIMIT}").json()
    assert len(entries) == 12
    assert [e["name"] for e in entries] == [f"file{i}.png" for i in range(11, -1, -1)]


def test_list_refs_still_filters_by_kind(client, assets_dir):
    _write_refs(assets_dir, 3)
    (assets_dir / "hashaud__voice.mp3").write_bytes(b"id3")

    assert [e["name"] for e in client.get("/refs?kind=audio").json()] == ["voice.mp3"]


@pytest.mark.parametrize("limit", [0, -1, 61, 999])
def test_list_refs_rejects_an_out_of_range_limit(client, assets_dir, limit):
    assert client.get(f"/refs?limit={limit}").status_code == 422
