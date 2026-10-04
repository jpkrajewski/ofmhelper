"""/character-sheet: several images in, one side-by-side PNG out."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ofmhelpers.utils.character_sheet import combine_side_by_side
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.image_tools import character_sheet


def png(path, size, color):
    Image.new("RGB", size, color).save(path)
    return path


def png_bytes(size=(9, 16)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-va", "next": "/"})
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


def test_run_keeps_upload_order_and_same_named_files(client, tmp_path, monkeypatch):
    """iOS names every shared photo image.jpg -- two of them must not
    overwrite each other."""
    monkeypatch.setattr(character_sheet, "UPLOAD_ROOT", tmp_path)
    monkeypatch.setattr(character_sheet, "enqueue", lambda *a, **k: None)

    r = client.post(
        "/character-sheet/run",
        files=[("files", ("image.png", png_bytes(), "image/png")) for _ in range(3)],
    )

    assert r.status_code == 200
    (job_dir,) = tmp_path.iterdir()
    assert sorted(p.name for p in job_dir.iterdir()) == [
        "00_image.png",
        "01_image.png",
        "02_image.png",
    ]


@pytest.mark.parametrize("count", [1, 11])
def test_run_rejects_too_few_or_too_many(client, count):
    r = client.post(
        "/character-sheet/run",
        files=[("files", (f"{i}.png", png_bytes(), "image/png")) for i in range(count)],
    )
    assert r.status_code == 400


def test_run_rejects_a_non_image(client):
    r = client.post(
        "/character-sheet/run",
        files=[
            ("files", ("a.png", png_bytes(), "image/png")),
            ("files", ("payload.html", b"<script>", "text/html")),
        ],
    )
    assert r.status_code == 400
