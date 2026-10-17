"""/clone: subject images + N videos -> one batch of Wan 3.0 runs, grouped on the page."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import json
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
from ofmhelpers.web.schemas import CloneBatch, Job, ResultFile
from ofmhelpers.web.stores.jobs import get_job, list_jobs

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)

NEW = json.dumps([{"kind": "new"}])


def _new(n: int) -> str:
    return json.dumps([{"kind": "new"}] * n)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", tmp_path)
    c = TestClient(app)
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


@pytest.fixture
def fake_runs(monkeypatch):
    """Video "a.mp4" is 2.6 s long, "b.mp4" 3.6 s, and so on; no kie.ai."""
    monkeypatch.setattr(
        clone,
        "duration_s",
        lambda path, _t: "abcdefghijklmnopqrstuvwxyz".index(Path(path).name[-5]) + 2.6,
    )
    monkeypatch.setattr(clone, "_run_wan3", lambda **_kwargs: [])


def _post(client, videos, data=None):
    files = [("image", ("face.png", b"img", "image/png"))] + [
        ("videos", (name, f"video-{name}".encode(), "video/mp4")) for name in videos
    ]
    form = {
        "api_key": "k",
        "image_manifest": NEW,
        "videos_manifest": _new(len(videos)),
        **(data or {}),
    }
    return client.post("/clone/run", data=form, files=files)


@pytest.mark.parametrize(
    ("seconds", "expected"), [(0.4, 2), (7.4, 7), (7.6, 8), (45.0, 30)]
)
def test_duration_is_the_video_length_clamped_to_wan3(seconds, expected):
    assert clone.wan3_duration(seconds) == expected


def test_page_defaults(client):
    html = client.get("/clone").text
    assert clone.DEFAULT_PROMPT in html
    assert '<option value="720P" selected>' in html
    assert 'data-field="image" data-kind="image" data-max="10"' in html
    assert 'class="file-add-input" multiple' in html


@pytest.mark.usefixtures("fake_runs")
def test_run_queues_one_batch_in_picked_order(client):
    r = _post(client, ["b.mp4", "a.mp4"])

    assert r.status_code == 200
    jobs = [get_job(j) for j in r.json()["job_ids"]]
    assert {j.task for j in jobs} == {"clone"}
    assert len({j.params["clone_batch"] for j in jobs}) == 1
    assert [j.params["clone_index"] for j in jobs] == [0, 1]
    assert [j.params["duration"] for j in jobs] == [4, 3]
    assert {j.params["resolution"] for j in jobs} == {"720P"}
    html = r.json()["html"]
    assert html.count('class="result-item"') == 2
    assert html.index("from b.mp4") < html.index("from a.mp4")
    assert "from b.mp4" in client.get("/clone").text


@pytest.mark.usefixtures("fake_runs")
def test_batches_fragment_shows_another_sessions_batch(client, tmp_path):
    """What live-gallery.js polls: a coworker's batch appears without a reload,
    and the VA scope still holds (an admin's batch never reaches a VA)."""
    coworker = TestClient(app)
    coworker.post("/login", data={"password": "test-va", "next": "/"})
    batch = get_job(_post(coworker, ["a.mp4"]).json()["job_ids"][0]).params[
        "clone_batch"
    ]
    admin = TestClient(app)
    admin.post("/login", data={"password": "test-admin", "next": "/"})
    admin_batch = get_job(_post(admin, ["b.mp4"]).json()["job_ids"][0]).params[
        "clone_batch"
    ]

    html = client.get("/clone/batches").text
    assert f'data-batch="{batch}"' in html
    assert f'data-batch="{admin_batch}"' not in html
    assert 'data-live-endpoint="/clone/batches"' in client.get("/clone").text


@pytest.mark.usefixtures("fake_runs")
def test_recreate_resubmits_the_same_files_without_uploading(client):
    first = get_job(_post(client, ["a.mp4", "b.mp4"]).json()["job_ids"][0])
    image = first.params["reference_images"]
    video = first.params["reference_videos"]

    r = client.post(
        "/clone/run",
        data={
            "api_key": "k",
            "image_manifest": json.dumps([{"kind": "existing", "path": image[0]}]),
            "videos_manifest": json.dumps([{"kind": "existing", "path": video[0]}]),
        },
    )

    assert r.status_code == 200
    [job_id] = r.json()["job_ids"]
    params = get_job(job_id).params
    assert params["reference_images"] == image
    assert params["reference_videos"] == video


@pytest.mark.usefixtures("fake_runs")
@pytest.mark.parametrize(
    ("videos", "data", "detail"),
    [
        ([], {}, "Pick between 1 and"),
        (["a.mp4"], {"image_manifest": "[]"}, "Pick between 1 and 10 subject images"),
        (["a.mp4"], {"api_key": " "}, "API key is required"),
    ],
)
def test_run_rejects_a_bad_batch(client, videos, data, detail):
    r = _post(client, videos, data)
    assert r.status_code == 400
    assert detail in r.json()["detail"]


@pytest.mark.usefixtures("fake_runs")
def test_every_run_gets_all_the_subject_images(client):
    files = [
        ("image", (name, name.encode(), "image/png"))
        for name in ("front.png", "side.png")
    ] + [("videos", (n, n.encode(), "video/mp4")) for n in ("a.mp4", "b.mp4")]
    r = client.post(
        "/clone/run",
        data={"api_key": "k", "image_manifest": _new(2), "videos_manifest": _new(2)},
        files=files,
    )

    assert r.status_code == 200
    jobs = [get_job(j) for j in r.json()["job_ids"]]
    images = jobs[0].params["reference_images"]
    assert [Path(p).name.split("__", 1)[1] for p in images] == ["front.png", "side.png"]
    assert all(j.params["reference_images"] == images for j in jobs)
    assert "2 images" in r.json()["html"]
    assert json.dumps(images) in _batches(jobs, page_full=False)[0].recreate_json


@pytest.mark.usefixtures("fake_runs")
def test_run_caps_subject_images(client, monkeypatch):
    monkeypatch.setenv("OFM_CLONE_MAX_IMAGES", "1")
    files = [("image", (n, n.encode(), "image/png")) for n in ("x.png", "y.png")]
    files.append(("videos", ("a.mp4", b"a", "video/mp4")))
    r = client.post(
        "/clone/run",
        data={"api_key": "k", "image_manifest": _new(2), "videos_manifest": NEW},
        files=files,
    )
    assert r.status_code == 400


@pytest.mark.usefixtures("fake_runs")
def test_run_caps_videos_per_batch(client, monkeypatch):
    monkeypatch.setenv("OFM_CLONE_MAX_VIDEOS", "2")
    r = _post(client, ["a.mp4", "b.mp4", "c.mp4"])
    assert r.status_code == 400


def test_one_unreadable_video_queues_nothing(client, monkeypatch):
    def probe(path, _timeout):
        if Path(path).name.endswith("bad.mp4"):
            raise ValueError
        return 5.0

    monkeypatch.setattr(clone, "duration_s", probe)
    before = len(list_jobs())
    r = _post(client, ["a.mp4", "bad.mp4"])
    assert r.status_code == 400
    assert "bad.mp4" in r.json()["detail"]
    assert len(list_jobs()) == before


def _batches(jobs, page_full):
    return CloneBatch.group([clone._card(j) for j in jobs], page_full=page_full)


def _job(job_id, batch, index, created_at):
    return Job(
        id=job_id,
        task="clone",
        status="running",
        created_at=created_at,
        params={
            "clone_batch": batch,
            "clone_index": index,
            "prompt": "p",
            "resolution": "720P",
            "audio": True,
            "duration": 5,
            "reference_images": ["/a/img.png"],
            "reference_videos": [f"/a/{job_id}.mp4"],
        },
    )


def test_batches_group_newest_first_and_drop_a_cut_off_tail():
    # Newest first, as the job store returns them.
    jobs = [
        _job("y2", "new", 1, 9.0),
        _job("y1", "new", 0, 9.0),
        _job("x1", "old", 1, 1.0),
    ]

    [new, old] = _batches(jobs, page_full=False)
    assert [c.job_id for c in new.cards] == ["y1", "y2"]
    assert json.loads(new.recreate_json)["reference_videos"] == [
        "/a/y1.mp4",
        "/a/y2.mp4",
    ]
    assert new.hue == _batches(jobs, page_full=False)[0].hue
    assert old.id == "old"
    assert [b.id for b in _batches(jobs, page_full=True)] == ["new"]


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


@needs_ffmpeg
def test_duration_s_reads_the_container_length(tmp_path):
    assert duration_s(_clip(tmp_path / "c.mp4", 3), 20) == pytest.approx(3.0, abs=0.2)


@needs_ffmpeg
def test_run_sends_kie_one_wan3_task_per_real_video(client, tmp_path, monkeypatch):
    """Real upload, real ffprobe, real Wan 3.0 runner -- only kie.ai's HTTP is
    faked, so this is exactly the payload a paid run would send."""
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
        data={"api_key": "k", "image_manifest": NEW, "videos_manifest": _new(2)},
        files=[
            ("image", ("face.png", b"img", "image/png")),
            *[("videos", (c.name, c.read_bytes(), "video/mp4")) for c in clips],
        ],
    )

    assert r.status_code == 200
    assert [model for model, _ in sent] == ["wan/3-0-video", "wan/3-0-video"]
    for (_model, payload), name, seconds in zip(sent, "ab", [3, 5], strict=True):
        assert payload["prompt"] == clone.DEFAULT_PROMPT
        assert payload["duration"] == seconds
        assert payload["resolution"] == "720P"
        assert payload["reference_image_urls"][0].endswith("face.png")
        assert payload["reference_video_urls"][0].endswith(f"{name}.mp4")
    statuses = [get_job(j).status for j in r.json()["job_ids"]]
    assert statuses == ["done", "done"]


def test_page_has_capcut_on_by_default(client):
    assert '<input type="checkbox" name="capcut" value="true" checked>' in (
        client.get("/clone").text
    )


@pytest.mark.usefixtures("fake_runs")
def test_capcut_choice_is_stored_and_recreated(client):
    on = get_job(_post(client, ["a.mp4"], {"capcut": "true"}).json()["job_ids"][0])
    off = get_job(_post(client, ["a.mp4"]).json()["job_ids"][0])

    assert on.params["capcut"] is True
    assert off.params["capcut"] is False
    [batch] = _batches([on], page_full=False)
    assert json.loads(batch.recreate_json)["capcut"] is True


def test_capcut_off_returns_the_generation_untouched(monkeypatch):
    result = [ResultFile(name="v.mp4", path="/x/v.mp4", remote_url="https://r/v")]
    monkeypatch.setattr(clone, "_run_wan3", lambda **_k: result)

    assert clone._run_clone("b", 0, capcut=False, assets_root="/x") == result


def test_capcut_keeps_a_remote_only_result(monkeypatch):
    result = [ResultFile(name="v.mp4", path=None, remote_url="https://r/v")]
    monkeypatch.setattr(clone, "_run_wan3", lambda **_k: result)

    assert clone._run_clone("b", 0, capcut=True, assets_root="/x") == result


@needs_ffmpeg
def test_capcut_on_serves_the_capcut_copy(tmp_path, monkeypatch):
    src = _clip(tmp_path / "gen.mp4", 2)
    monkeypatch.setattr(
        clone,
        "_run_wan3",
        lambda **_k: [ResultFile(name=src.name, path=str(src), remote_url="https://r")],
    )
    assets = tmp_path / "assets"

    [result] = clone._run_clone("b", 0, capcut=True, assets_root=str(assets))

    out = Path(result.path)
    assert result == ResultFile(name="gen_capcut.mp4", path=str(out), remote_url=None)
    assert out.exists()
    assert src.exists()  # the original stays, and stays registered elsewhere
    assert [p.name.split("__", 1)[1] for p in assets.iterdir()] == [out.name]


def test_capcut_failure_keeps_the_generated_video(monkeypatch):
    result = [ResultFile(name="v.mp4", path="/x/v.mp4", remote_url="https://r/v")]
    monkeypatch.setattr(clone, "_run_wan3", lambda **_k: result)

    def broken(*_a, **_k):
        msg = "ffmpeg failed: boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(clone, "apply_preset", broken)

    assert clone._run_clone("b", 0, capcut=True, assets_root="/x") == result
