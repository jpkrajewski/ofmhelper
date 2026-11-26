# Copyright (C) 2026 jpkrajewski

import io

import pillow_heif
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from signin import sign_in_agency

from ofmhelpers.features.character_sheet import service as character_sheet
from ofmhelpers.media.character_sheet import combine_side_by_side
from ofmhelpers.web.main import app


def png(path, size, color):
    Image.new("RGB", size, color).save(path)
    return path


def png_bytes(size=(9, 16)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size).save(buf, "PNG")
    return buf.getvalue()


def heic_bytes() -> bytes:
    buf = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", (9, 16))).save(buf)
    return buf.getvalue()


@pytest.fixture
def client():
    c = TestClient(app)
    sign_in_agency(c)
    return c


def test_panels_sit_side_by_side_in_order_with_no_gap(tmp_path):
    red = png(tmp_path / "a.png", (90, 160), (255, 0, 0))
    blue = png(tmp_path / "b.png", (90, 160), (0, 0, 255))

    out = combine_side_by_side([red, blue], tmp_path / "sheet.png")

    with Image.open(out) as sheet:
        assert sheet.format == "PNG"
        assert sheet.size == (180, 160)
        assert sheet.getpixel((89, 80)) == (255, 0, 0)
        assert sheet.getpixel((90, 80)) == (0, 0, 255)


def test_smaller_panels_scale_up_to_the_tallest(tmp_path):
    big = png(tmp_path / "big.png", (90, 160), (0, 0, 0))
    small = png(tmp_path / "small.png", (45, 80), (0, 0, 0))

    out = combine_side_by_side([big, small], tmp_path / "sheet.png")

    with Image.open(out) as sheet:
        assert sheet.size == (180, 160)


@pytest.mark.parametrize("count", [2, 3, 4])
def test_run_keeps_upload_order_and_same_named_files(
    client,
    tmp_path,
    monkeypatch,
    count,
):
    monkeypatch.setattr(character_sheet, "upload_root", lambda: tmp_path)
    monkeypatch.setattr(character_sheet, "enqueue", lambda *a, **k: None)

    uploads = [
        ("files", ("image.png", png_bytes((9 + index, 16)), "image/png"))
        for index in range(count)
    ]
    r = client.post("/character-sheet/run", files=uploads)

    assert r.status_code == 202
    (job_dir,) = tmp_path.iterdir()
    expected_names = [f"{index:02d}_image.png" for index in range(count)]
    assert sorted(path.name for path in job_dir.iterdir()) == expected_names
    for index in range(count):
        with Image.open(job_dir / f"{index:02d}_image.png") as image:
            assert image.size == (9 + index, 16)


def test_run_converts_heic_before_saving(client, tmp_path, monkeypatch):
    monkeypatch.setattr(character_sheet, "upload_root", lambda: tmp_path)
    monkeypatch.setattr(character_sheet, "enqueue", lambda *a, **k: None)

    r = client.post(
        "/character-sheet/run",
        files=[
            ("files", ("photo.heic", heic_bytes(), "image/heic")),
            ("files", ("image.png", png_bytes(), "image/png")),
        ],
    )

    assert r.status_code == 202
    (job_dir,) = tmp_path.iterdir()
    assert sorted(p.name for p in job_dir.iterdir()) == [
        "00_photo.jpg",
        "01_image.png",
    ]
    with Image.open(job_dir / "00_photo.jpg") as image:
        assert image.format == "JPEG"


def named_png_uploads(count: int):
    return [
        ("files", (f"{index}.png", png_bytes(), "image/png")) for index in range(count)
    ]


@pytest.mark.parametrize("count", [0, 1, 5])
def test_run_rejects_too_few_or_too_many(client, count):
    r = client.post("/character-sheet/run", files=named_png_uploads(count))
    assert r.status_code == 422


def test_run_reads_file_limits_at_request_time(client, monkeypatch):
    monkeypatch.setenv("OFM_CHARACTER_SHEET_MAX_IMAGES", "2")
    r = client.post("/character-sheet/run", files=named_png_uploads(3))
    assert r.status_code == 422


@pytest.mark.parametrize("filename", ["payload.html", "payload.png"])
def test_run_rejects_a_non_image(client, filename):
    r = client.post(
        "/character-sheet/run",
        files=[
            ("files", ("a.png", png_bytes(), "image/png")),
            ("files", (filename, b"<script>", "text/html")),
        ],
    )
    assert r.status_code == 422


def test_run_rejects_a_png_with_bad_checksum(client):
    corrupted = bytearray(png_bytes())
    idat = corrupted.index(b"IDAT")
    size = int.from_bytes(corrupted[idat - 4 : idat])
    corrupted[idat + 4 + size] ^= 1
    r = client.post(
        "/character-sheet/run",
        files=[
            ("files", ("a.png", png_bytes(), "image/png")),
            ("files", ("bad.png", bytes(corrupted), "image/png")),
        ],
    )
    assert r.status_code == 422
