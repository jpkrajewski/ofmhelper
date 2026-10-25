"""`fitted` / `fit_image`: a shrunk or converted JPEG copy that only ever
appears whole (2026-10-25 review #6: PIL truncates its target before it
encodes, so a worker reading the copy mid-write uploaded half a JPEG)."""

import io
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from ofmhelpers.utils.image_fit import ImageLimits, fitted

LIMITS = ImageLimits(max_bytes=10**9, max_side=64)


@pytest.fixture
def oversized(tmp_path):
    path = tmp_path / "abc__sheet.png"
    Image.effect_noise((256, 256), 64).convert("RGB").save(path)
    return path


@pytest.fixture
def slow_writes(monkeypatch):
    """PIL's own order, slowed down: open (truncate) the target, write half,
    then the rest -- the window a second worker used to read in."""
    real_save = Image.Image.save

    def save(self, fp, fmt=None, **params):
        if not isinstance(fp, str | Path):
            return real_save(self, fp, fmt, **params)
        buffer = io.BytesIO()
        real_save(self, buffer, fmt, **params)
        data = buffer.getvalue()
        with Path(fp).open("wb") as out:
            out.write(data[: len(data) // 2])
            out.flush()
            time.sleep(0.1)
            out.write(data[len(data) // 2 :])
        return None

    monkeypatch.setattr(Image.Image, "save", save)


def test_workers_fitting_one_image_at_once_all_read_the_whole_copy(
    tmp_path, oversized, slow_writes
):
    dest_dir = tmp_path / "fitted"
    start = threading.Barrier(3)
    read: list[bytes] = []

    def worker(delay: float) -> None:
        start.wait()
        time.sleep(delay)  # some arrive while the first is still writing
        read.append(fitted(oversized, dest_dir, LIMITS).read_bytes())

    threads = [threading.Thread(target=worker, args=(d,)) for d in (0, 0.03, 0.06)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    final = (dest_dir / "abc__sheet.jpg").read_bytes()
    assert read == [final] * 3
    with Image.open(io.BytesIO(final)) as img:
        img.load()
        assert img.size == (64, 64)
    assert [p.name for p in dest_dir.iterdir()] == ["abc__sheet.jpg"]


def test_an_encode_that_fails_leaves_no_copy_to_reuse(tmp_path, oversized, monkeypatch):
    """A full disk (or a deploy's SIGKILL) mid-encode: the next run makes the
    copy again instead of reusing a broken one forever."""
    real_save = Image.Image.save

    def failing(self, fp, fmt=None, **params):
        if not isinstance(fp, str | Path):
            return real_save(self, fp, fmt, **params)
        Path(fp).write_bytes(b"\xff\xd8 half a jpeg")
        msg = "No space left on device"
        raise OSError(msg)

    monkeypatch.setattr(Image.Image, "save", failing)
    dest_dir = tmp_path / "fitted"

    with pytest.raises(OSError, match="No space"):
        fitted(oversized, dest_dir, LIMITS)

    assert list(dest_dir.iterdir()) == []


@pytest.mark.parametrize(("suffix", "fmt"), [(".tiff", "TIFF"), (".bmp", "BMP")])
def test_jpeg_converts_an_image_that_already_fits(tmp_path, suffix, fmt):
    src = tmp_path / f"face{suffix}"
    Image.new("RGB", (8, 6), "blue").save(src, fmt)

    copy = fitted(src, tmp_path / "fitted", LIMITS, jpeg=True)

    assert copy == tmp_path / "fitted" / "face.jpg"
    with Image.open(copy) as img:
        assert (img.format, img.size) == ("JPEG", (8, 6))


def test_an_image_that_fits_is_sent_as_it_is(tmp_path):
    src = tmp_path / "face.png"
    Image.new("RGB", (8, 6)).save(src)

    assert fitted(src, tmp_path / "fitted", LIMITS) == src
    assert not (tmp_path / "fitted").exists()


def test_jpeg_leaves_a_gif_alone(tmp_path):
    """A JPEG copy would drop a GIF's animation."""
    src = tmp_path / "loop.gif"
    Image.new("RGB", (8, 6)).save(src, "GIF")

    assert fitted(src, tmp_path / "fitted", LIMITS, jpeg=True) == src
