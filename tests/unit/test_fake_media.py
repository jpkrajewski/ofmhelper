"""utils/fake_media.py: the fakes' mp4, made once even when many threads ask
for it first (a reel download pool did: two threads raced on one temp file,
`FileNotFoundError` on the rename)."""

from concurrent.futures import ThreadPoolExecutor

from ofmhelpers.utils import fake_media


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
