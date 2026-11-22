import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ofmhelpers.media.repurpose.checker import tmk


@pytest.fixture
def hashed(monkeypatch, tmp_path) -> list[Path]:
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
    tmk._source("/videos/used.mp4", 1.0, 30)

    new = tmk._source("/videos/new.mp4", 1.0, 30)

    assert sorted(tmk.sources_dir().iterdir()) == sorted([used, new])


def test_every_tmk_score_carries_the_match_line(hashed, monkeypatch, tmp_path):
    answers = iter(["0.81 0.92 source.tmk copy.tmk", ""])
    monkeypatch.setattr(
        tmk.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=next(answers)),
    )
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    scored = tmk.compare(source, tmp_path / "copy.mp4", 30)
    unscored = tmk.compare(source, tmp_path / "copy.mp4", 30)

    assert (scored.level1, scored.level2) == (0.81, 0.92)
    assert (scored.threshold, scored.matched) == (tmk.THRESHOLD, True)
    assert (unscored.threshold, unscored.matched) == (tmk.THRESHOLD, False)
