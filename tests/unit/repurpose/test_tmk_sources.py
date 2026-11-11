"""checker/tmk.py keeps each source's hash in the uploads root's `.tmk`
folder (it was the container's temp dir: never cleaned), made once per file
version and dropped a day after its last use."""

import os
import time
from pathlib import Path

import pytest

from ofmhelpers.media.repurpose.checker import tmk


@pytest.fixture
def hashed(monkeypatch, tmp_path) -> list[Path]:
    """The uploads root in `tmp_path`; the videos the C++ tool was run on."""
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    made: list[Path] = []

    def fake_hash(video: Path, dst: Path, timeout_s: int) -> Path:
        made.append(video)
        dst.write_bytes(b"tmk")
        return dst

    monkeypatch.setattr(tmk, "_hash", fake_hash)
    return made


def test_a_sources_hash_is_kept_under_the_uploads_root(hashed, tmp_path):
    stored = tmk._source("/videos/a.mp4", 1.0, 30)

    assert stored.parent == tmp_path / "uploads" / ".tmk"
    assert stored.read_bytes() == b"tmk"
    assert list(stored.parent.glob("*.part")) == []


def test_the_same_file_version_is_hashed_once(hashed):
    first = tmk._source("/videos/a.mp4", 1.0, 30)
    again = tmk._source("/videos/a.mp4", 1.0, 30)
    changed = tmk._source("/videos/a.mp4", 2.0, 30)

    assert again == first
    assert changed != first
    assert hashed == [Path("/videos/a.mp4"), Path("/videos/a.mp4")]


def test_a_hash_unused_for_a_day_goes_when_the_next_is_made(hashed):
    old = tmk._source("/videos/old.mp4", 1.0, 30)
    used = tmk._source("/videos/used.mp4", 1.0, 30)
    long_ago = time.time() - tmk._KEEP_S - 60
    os.utime(old, (long_ago, long_ago))
    os.utime(used, (long_ago, long_ago))
    tmk._source("/videos/used.mp4", 1.0, 30)  # used again: its day starts over

    new = tmk._source("/videos/new.mp4", 1.0, 30)

    assert sorted(tmk.sources_dir().iterdir()) == sorted([used, new])
