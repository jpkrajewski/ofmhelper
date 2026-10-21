"""/uniqify: one video in, N re-encoded variants out (utils/uniqify.py)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import json
import random
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.utils.uniqify import (
    PRESETS,
    MetadataMode,
    Strength,
    VariantParams,
    generate_variants,
)
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.video_tools import uniqify


def _write_clip(dest: Path, width: int = 1280, height: int = 720) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:duration=1:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height:format_tags",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


def test_presets_are_two_percent_stronger_than_the_original_tool():
    hard = PRESETS[Strength.HARD]
    assert hard.speed == pytest.approx((1 - 0.06 * 1.02, 1 + 0.06 * 1.02))
    assert hard.brightness == pytest.approx((-0.03 * 1.02, 0.03 * 1.02))
    assert hard.noise == pytest.approx((0.0, 9.0 * 1.02))


def test_sampled_params_stay_inside_the_preset():
    preset = PRESETS[Strength.EXTREME]
    rng = random.Random(1)  # noqa: S311 - jitter, not cryptography
    for _ in range(50):
        p = VariantParams.sample(preset, rng, mirror_pct=0, trim_secs=0.5)
        assert preset.speed[0] <= p.speed <= preset.speed[1]
        assert 0 <= p.trim_head <= 0.5
        assert not p.mirror


def test_mirror_pct_100_mirrors_every_copy():
    rng = random.Random(1)  # noqa: S311 - jitter, not cryptography
    params = [
        VariantParams.sample(PRESETS[Strength.MEDIUM], rng, 100, 0) for _ in range(10)
    ]
    assert all(p.mirror for p in params)
    assert all("hflip" in p.video_filter() for p in params)


def test_variants_are_named_capped_and_distinct(tmp_path):
    src = _write_clip(tmp_path / "src.mp4")

    outputs = generate_variants(
        src, tmp_path, "clip", 3, Strength.HARD, 0, 0.1, MetadataMode.STRIP, 60, seed=7
    )

    assert [p.name for p in outputs] == ["clip_001.mp4", "clip_002.mp4", "clip_003.mp4"]
    streams = _probe(outputs[0])["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    assert video["width"] == 720
    assert video["height"] % 2 == 0
    assert any(s["codec_type"] == "audio" for s in streams)
    assert len({p.read_bytes() for p in outputs}) == 3


def test_device_metadata_writes_a_recent_creation_time(tmp_path):
    src = _write_clip(tmp_path / "src.mp4", 320, 240)

    [out] = generate_variants(
        src, tmp_path, "clip", 1, Strength.MINIMAL, 0, 0, MetadataMode.DEVICE, 60
    )

    tags = _probe(out)["format"]["tags"]
    assert tags["major_brand"].strip() == "qt"
    assert tags["creation_time"].startswith("20")


def test_run_end_to_end_serves_every_copy(client, tmp_path, monkeypatch):
    monkeypatch.setattr(uniqify, "UPLOAD_ROOT", tmp_path)
    clip = _write_clip(tmp_path / "in.mp4", 320, 240).read_bytes()

    r = client.post(
        "/uniqify/run",
        data={"copies": "2", "strength": "minimal", "output_name": "promo"},
        files={"file": ("in.mp4", clip, "video/mp4")},
    )

    assert r.status_code == 200
    status = client.get(f"/uniqify/jobs/{r.json()['job_id']}/status").json()
    assert status["status"] == "done"
    [archive] = status["result"]
    assert archive["name"] == "promo.zip"

    download = client.get(archive["download_url"])
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        assert zf.namelist() == ["promo_001.mp4", "promo_002.mp4"]
    # Only the uploaded source and the zip are left on disk.
    [job_dir] = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert sorted(p.name for p in job_dir.iterdir()) == ["promo.zip", "source_in.mp4"]
    assert "promo.zip" in client.get("/uniqify").text


def test_run_rejects_a_non_video(client):
    r = client.post(
        "/uniqify/run", files={"file": ("page.html", b"<script>", "text/html")}
    )
    assert r.status_code == 400


@pytest.mark.parametrize("copies", ["0", "51"])
def test_run_rejects_copies_out_of_range(client, copies):
    r = client.post(
        "/uniqify/run",
        data={"copies": copies},
        files={"file": ("in.mp4", b"x", "video/mp4")},
    )
    assert r.status_code == 422
