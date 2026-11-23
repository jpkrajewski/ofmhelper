# Copyright (C) 2026 jpkrajewski

from concurrent.futures import ThreadPoolExecutor

from ofmhelpers.media import fake_media


def test_many_first_callers_get_the_same_playable_mp4(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_FAKES_DIR", str(tmp_path))
    fake_media.mp4.cache_clear()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            bodies = list(pool.map(lambda _: fake_media.mp4(), range(8)))
    finally:
        fake_media.mp4.cache_clear()
    assert len(set(bodies)) == 1
    assert bodies[0][4:8] == b"ftyp"
    assert [p.name for p in tmp_path.iterdir()] == ["fake-reel.mp4"]
