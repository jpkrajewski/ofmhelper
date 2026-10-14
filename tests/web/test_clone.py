"""/clone: one image + N videos -> one wan3 job per video, duration = video length."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.utils.video_probe import duration_s
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.generation import clone
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.stores.jobs import get_job


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", tmp_path)
    c = TestClient(app)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


@pytest.mark.parametrize(
    ("seconds", "expected"), [(0.4, 2), (7.4, 7), (7.6, 8), (45.0, 30)]
)
def test_duration_is_the_video_length_clamped_to_wan3(seconds, expected):
    assert clone.wan3_duration(seconds) == expected


def test_page_prefills_the_default_prompt(client):
    html = client.get("/clone").text
    assert clone.DEFAULT_PROMPT in html
    assert 'name="videos"' in html


def test_run_queues_one_wan3_job_per_video(client, monkeypatch):
    lengths = {"a.mp4": 6.2, "b.mp4": 12.9}
    monkeypatch.setattr(
        clone,
        "duration_s",
        lambda path, _timeout: lengths[Path(path).name.split("__", 1)[1]],
    )
    monkeypatch.setattr(clone, "_run_wan3", lambda **_kwargs: [])

    r = client.post(
        "/clone/run",
        data={"api_key": "k"},
        files=[
            ("image", ("face.png", b"img", "image/png")),
            ("videos", ("a.mp4", b"video-a", "video/mp4")),
            ("videos", ("b.mp4", b"video-b", "video/mp4")),
        ],
    )

    assert r.status_code == 200
    jobs = [get_job(j) for j in r.json()["job_ids"]]
    assert [j["task"] for j in jobs] == ["wan3", "wan3"]
    assert [j["params"]["duration"] for j in jobs] == [6, 13]
    assert all(j["params"]["prompt"] == clone.DEFAULT_PROMPT for j in jobs)
    assert (
        jobs[0]["params"]["reference_images"] == jobs[1]["params"]["reference_images"]
    )
    assert [Path(j["params"]["reference_videos"][0]).name[-5:] for j in jobs] == [
        "a.mp4",
        "b.mp4",
    ]


def test_run_rejects_an_unreadable_video(client, monkeypatch):
    def fail(*_args):
        raise ValueError

    monkeypatch.setattr(clone, "duration_s", fail)
    r = client.post(
        "/clone/run",
        data={"api_key": "k"},
        files=[
            ("image", ("face.png", b"img", "image/png")),
            ("videos", ("a.mp4", b"x", "video/mp4")),
        ],
    )
    assert r.status_code == 400


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_duration_s_reads_the_container_length(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=64x64:duration=3:rate=10",
            str(clip),
        ],
        check=True,
    )
    assert duration_s(clip, 20) == pytest.approx(3.0, abs=0.2)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_run_sends_kie_one_wan3_task_per_real_video(client, tmp_path, monkeypatch):
    """Real upload, real ffprobe, real _run_wan3 -- only kie.ai's HTTP is faked,
    so this is exactly the payload a paid run would send."""
    sent = []

    def create_task(_self, model, payload, callback_url=None):
        sent.append((model, payload))
        return f"t{len(sent)}"

    def download_urls(_self, *_args):
        raise OSError

    monkeypatch.setattr(
        KieAIClient, "upload_local_file", lambda _self, p: f"https://up/{Path(p).name}"
    )
    monkeypatch.setattr(KieAIClient, "create_task", create_task)
    monkeypatch.setattr(
        KieAIClient, "poll_task", lambda _self, tid, **_k: [f"https://res/{tid}.mp4"]
    )
    monkeypatch.setattr(KieAIClient, "download_urls", download_urls)
    clips = [_clip(tmp_path / "a.mp4", 3), _clip(tmp_path / "b.mp4", 5)]

    r = client.post(
        "/clone/run",
        data={"api_key": "k"},
        files=[
            ("image", ("face.png", b"img", "image/png")),
            *[("videos", (c.name, c.read_bytes(), "video/mp4")) for c in clips],
        ],
    )

    assert r.status_code == 200
    assert [model for model, _ in sent] == ["wan/3-0-video", "wan/3-0-video"]
    for (_model, payload), name, seconds in zip(sent, ["a", "b"], [3, 5], strict=True):
        assert payload["prompt"] == clone.DEFAULT_PROMPT
        assert payload["duration"] == seconds
        assert payload["resolution"] == "1080P"
        assert payload["reference_image_urls"][0].endswith("face.png")
        assert payload["reference_video_urls"][0].endswith(f"{name}.mp4")
    statuses = [get_job(j)["status"] for j in r.json()["job_ids"]]
    assert statuses == ["done", "done"]


def _clip(dest: Path, seconds: int) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=64x64:duration={seconds}:rate=10",
            str(dest),
        ],
        check=True,
    )
    return dest
