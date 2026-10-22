"""
Slow connections never saw /clone results (4K exports, ~28 Mbit/s, 30-40 MB)
or /generate images (6.5 MB PNGs) -- prod, 2026-10-21. Galleries now show
previews: a 720p faststart mp4 behind a first-frame poster for a video, a
small webp for an image. They are made when a job finishes (run_job queues
them on the previews queue), for older jobs by a startup backfill, and on
request as the last resort. Download still serves the original.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ofmhelpers.cache import PREVIEWS_QUEUE
from ofmhelpers.web import preview_backfill, previews
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import asset_card
from ofmhelpers.web.stores import jobs as jobs_store
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job

pytestmark = pytest.mark.filterwarnings("ignore")

PREFIX = "/fake-ai"


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture(autouse=True)
def uploads_root(monkeypatch, tmp_path):
    """Each test's previews in a folder of its own."""
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))


def _clip(path: Path, width: int, height: int) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:rate=25:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1",
            "-c:v",
            "libx264",
            "-b:v",
            "20M",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def _picture(path: Path, width: int, height: int) -> Path:
    Image.new("RGB", (width, height), (200, 30, 90)).save(path)
    return path


def _probe_height(path: Path) -> int:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=height",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)["streams"][0]["height"]


def _done_job(*files: Path) -> str:
    job_id = create_job("fake_ai", {"prompt": "p"})
    run_job(job_id, lambda: [{"name": f.name, "path": str(f)} for f in files], {})
    return job_id


@pytest.fixture
def no_warming(monkeypatch):
    """run_job queues nothing: what a job finished before previews looks
    like."""
    monkeypatch.setattr(jobs_store, "_queue_previews", lambda job_id: None)


# --- what a card offers ---------------------------------------------------


def test_a_local_video_offers_a_preview_and_a_poster():
    card = asset_card("out.mp4", 0, f"{PREFIX}/files/j1")
    assert card.preview_url == f"{PREFIX}/files/j1/0/preview"
    assert card.poster_url == f"{PREFIX}/files/j1/0/poster"
    assert card.download_url == f"{PREFIX}/files/j1/0?dl=1"  # still the original


def test_a_local_image_offers_a_preview_and_no_poster():
    card = asset_card("out.png", 0, f"{PREFIX}/files/j1")
    assert card.preview_url == f"{PREFIX}/files/j1/0/preview"
    assert card.poster_url is None


def test_a_remote_only_result_offers_neither():
    card = asset_card(
        "out.mp4", 0, f"{PREFIX}/files/j1", remote_url="https://x/o.mp4", local=False
    )
    assert card.preview_url is None
    assert card.poster_url is None


def test_a_local_copy_previews_even_when_kie_hosts_one(client, tmp_path):
    """The gallery shows our small preview, not kie.ai's full PNG."""
    job_id = create_job("fake_ai", {"prompt": "p"})
    pic = _picture(tmp_path / "r.png", 64, 64)
    run_job(
        job_id,
        lambda: [{"name": pic.name, "path": str(pic), "remote_url": "https://k/r.png"}],
        {},
    )

    html = client.get("/generate").text

    assert f'src="{PREFIX}/files/{job_id}/0/preview"' in html
    assert 'loading="lazy"' in html


def test_status_payload_carries_the_preview_urls(client, tmp_path):
    clip = tmp_path / "r.mp4"
    clip.write_bytes(b"not decoded here")
    job_id = _done_job(clip)

    (item,) = client.get(f"{PREFIX}/jobs/{job_id}/status").json()["result"]

    assert item["preview_url"] == f"{PREFIX}/files/{job_id}/0/preview"
    assert item["poster_url"] == f"{PREFIX}/files/{job_id}/0/poster"


# --- the routes -----------------------------------------------------------


def test_an_image_preview_is_a_small_cached_webp(client, tmp_path, no_warming):
    pic = _picture(tmp_path / "big.png", 3000, 4000)
    job_id = _done_job(pic)

    r = client.get(f"{PREFIX}/files/{job_id}/0/preview")

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert "immutable" in r.headers["cache-control"]
    (cached,) = previews.previews_dir().glob("*.webp")
    with Image.open(cached) as img:
        assert max(img.size) == 1024
    assert len(r.content) < pic.stat().st_size


def test_a_preview_that_cannot_be_made_serves_the_original(client, tmp_path):
    pic = tmp_path / "r.png"
    pic.write_bytes(b"not a png")
    job_id = _done_job(pic)

    r = client.get(f"{PREFIX}/files/{job_id}/0/preview")

    assert r.status_code == 200
    assert r.content == b"not a png"


def test_an_image_has_no_poster_and_audio_no_preview(client, tmp_path):
    pic = _picture(tmp_path / "r.png", 8, 8)
    voice = tmp_path / "v.mp3"
    voice.write_bytes(b"id3")
    job_id = _done_job(pic, voice)

    assert client.get(f"{PREFIX}/files/{job_id}/0/poster").status_code == 404
    assert client.get(f"{PREFIX}/files/{job_id}/1/preview").status_code == 404


def test_an_unknown_job_has_no_preview(client):
    assert client.get(f"{PREFIX}/files/nope/0/preview").status_code == 404


def test_originals_are_cached_for_good(client, tmp_path):
    pic = _picture(tmp_path / "r.png", 8, 8)
    job_id = _done_job(pic)

    for url in (f"{PREFIX}/files/{job_id}/0", f"{PREFIX}/files/{job_id}/0?dl=1"):
        assert "immutable" in client.get(url).headers["cache-control"]


# --- made ahead of the first viewer ---------------------------------------


def test_a_finished_job_queues_its_previews_on_the_previews_queue(
    tmp_path, monkeypatch
):
    queued = []
    monkeypatch.setattr(
        jobs_store,
        "enqueue_to",
        lambda queue, fn, *args: queued.append((queue, fn, args)),
    )
    pic = _picture(tmp_path / "r.png", 8, 8)

    job_id = _done_job(pic)

    assert queued == [(PREVIEWS_QUEUE, previews.warm_previews, (job_id, [str(pic)]))]


def test_a_failed_job_queues_nothing(monkeypatch):
    queued = []
    monkeypatch.setattr(jobs_store, "enqueue_to", lambda *a: queued.append(a))
    job_id = create_job("fake_ai", {"prompt": "p"})

    def boom():
        msg = "provider said no"
        raise RuntimeError(msg)

    run_job(job_id, boom, {})

    assert queued == []


def test_warmed_previews_are_what_the_route_serves(client, tmp_path, monkeypatch):
    """Tests enqueue inline, so the preview exists once the job is done;
    the route then makes nothing."""
    pic = _picture(tmp_path / "r.png", 2000, 2000)
    job_id = _done_job(pic)
    assert previews.is_warm(job_id, [str(pic)])

    def no_encode(*a):
        msg = "route re-made a warmed preview"
        raise AssertionError(msg)

    monkeypatch.setattr(previews, "write_image_thumb", no_encode)
    assert client.get(f"{PREFIX}/files/{job_id}/0/preview").status_code == 200


def test_backfill_queues_only_jobs_missing_previews(tmp_path, monkeypatch, no_warming):
    old = _done_job(_picture(tmp_path / "old.png", 8, 8))
    done = _done_job(_picture(tmp_path / "done.png", 8, 8))
    job = get_job(done)
    assert job is not None
    previews.warm_previews(done, job.local_paths())
    create_job("fake_ai", {"prompt": "still running"})

    queued = []
    monkeypatch.setattr(
        preview_backfill,
        "enqueue_to",
        lambda queue, fn, job_id, sources: queued.append(job_id),
    )

    assert preview_backfill.backfill() == 1
    assert queued == [old]
    # Another process starting at the same time: the guard holds.
    assert preview_backfill.backfill() == 0


def test_backfill_makes_the_previews(tmp_path, no_warming):
    pic = _picture(tmp_path / "old.png", 8, 8)
    job_id = _done_job(pic)

    preview_backfill.backfill()

    assert previews.is_warm(job_id, [str(pic)])


# --- polling and compression ----------------------------------------------


@pytest.mark.parametrize("endpoint", ["/generate/gallery/latest", "/clone/batches"])
def test_an_unchanged_live_gallery_answers_304(client, tmp_path, endpoint):
    _done_job(_picture(tmp_path / "r.png", 8, 8))
    first = client.get(endpoint)
    etag = first.headers["etag"]
    assert "no-cache" in first.headers["cache-control"]

    again = client.get(endpoint, headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.content == b""


def test_a_new_job_changes_the_live_gallery(client, tmp_path):
    _done_job(_picture(tmp_path / "a.png", 8, 8))
    etag = client.get("/generate/gallery/latest").headers["etag"]

    _done_job(_picture(tmp_path / "b.png", 8, 8))

    r = client.get("/generate/gallery/latest", headers={"If-None-Match": etag})
    assert r.status_code == 200
    assert r.headers["etag"] != etag


def test_pages_css_and_json_are_gzipped(client):
    for url in ("/generate", "/static/css/app.css"):
        r = client.get(url, headers={"Accept-Encoding": "gzip"})
        assert r.headers.get("content-encoding") == "gzip", url


def test_media_and_downloads_are_never_gzipped(client, tmp_path):
    pic = _picture(tmp_path / "r.png", 400, 400)
    job_id = _done_job(pic)

    for url in (f"{PREFIX}/files/{job_id}/0", f"{PREFIX}/files/{job_id}/0?dl=1"):
        r = client.get(url, headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in r.headers, url
        assert r.content == pic.read_bytes()


# --- real encoding --------------------------------------------------------


@pytest.mark.heavy
def test_video_preview_is_720p_smaller_faststart(client, tmp_path, no_warming):
    clip = _clip(tmp_path / "big.mp4", 1080, 1920)
    job_id = _done_job(clip)

    r = client.get(f"{PREFIX}/files/{job_id}/0/preview")

    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    (cached,) = previews.previews_dir().glob("*.mp4")
    assert _probe_height(cached) == 720
    assert cached.stat().st_size < clip.stat().st_size / 3
    # faststart: the index (moov) comes before the media data (mdat).
    assert b"moov" in cached.read_bytes()[:4096]


@pytest.mark.heavy
def test_a_small_video_is_never_upscaled(client, tmp_path, no_warming):
    clip = _clip(tmp_path / "small.mp4", 320, 568)
    job_id = _done_job(clip)

    assert client.get(f"{PREFIX}/files/{job_id}/0/preview").status_code == 200

    (cached,) = previews.previews_dir().glob("*.mp4")
    assert _probe_height(cached) == 568


@pytest.mark.heavy
def test_poster_is_a_small_webp_frame(client, tmp_path, no_warming):
    clip = _clip(tmp_path / "big.mp4", 1080, 1920)
    job_id = _done_job(clip)

    r = client.get(f"{PREFIX}/files/{job_id}/0/poster")

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert r.content[:4] == b"RIFF"
    assert len(r.content) < 100_000


@pytest.mark.heavy
def test_a_finished_video_job_is_warmed_with_preview_and_poster(tmp_path):
    clip = _clip(tmp_path / "big.mp4", 1080, 1920)

    job_id = _done_job(clip)

    assert previews.preview_path(job_id, 0, "video").is_file()
    assert previews.poster_path(job_id, 0).is_file()


def test_backfill_never_deletes_a_job_whose_files_are_gone(tmp_path, no_warming):
    """Dev on a prod snapshot (rows without files), or a volume not mounted
    yet at startup: the pass skips such jobs. It once listed jobs through the
    self-healing list, which deleted every one of them (dev, 2026-10-21)."""
    pic = _picture(tmp_path / "gone.png", 8, 8)
    job_id = _done_job(pic)
    pic.unlink()

    assert preview_backfill.backfill() == 0
    assert get_job(job_id) is not None
