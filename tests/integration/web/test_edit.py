"""/edit (routers/video_tools/edit.py, features/edit): open a stored video,
its frame strip, a cut queued on the edit worker (inline in the suite) and
polled, the limits a request cannot get past, nothing kept in the Reuse
pickers, and the sweep that deletes what /edit made after a day."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job, get_job
from ofmhelpers.features.edit import cleanup
from ofmhelpers.features.edit import service as edit_service
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.task_helpers import assets_root_for_user
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads

pytestmark = pytest.mark.heavy  # real ffmpeg encodes


def _video(dest: Path, seconds: float = 4, size: str = "640x360") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:duration={seconds}:rate=25",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            "-shortest",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest.resolve()


def _shape(path: str) -> tuple[int, int, float]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return stream["width"], stream["height"], float(data["format"]["duration"])


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", root)
    return root


@pytest.fixture
def client() -> TestClient:
    c = TestClient(app)
    sign_in(c)
    return c


@pytest.fixture
def mine(client) -> Path:
    """My part of the store, where a 4 s 640x360 video waits."""
    root = assets_root_for_user(make_user().id)
    _video(root / f"{'a' * 64}__clip.mp4")
    return root


def _clip(root: Path) -> str:
    return str((root / f"{'a' * 64}__clip.mp4").resolve())


def _cut(client, video: str, start: float, end: float):
    return client.post("/edit/cut", json={"video": video, "start": start, "end": end})


def test_the_page_opens_my_video_with_its_strip(client, mine):
    page = client.get("/edit", params={"video": _clip(mine)}).text

    assert 'id="edit"' in page
    assert 'data-seconds="4.0' in page
    assert "/edit/strip?video=" in page


def test_a_gone_video_opens_the_page_empty_with_the_reason(client, mine):
    page = client.get("/edit", params={"video": str(mine / "nope.mp4")}).text

    assert 'id="edit"' not in page
    assert "That video is gone: pick it again" in page
    assert 'class="field file-picker"' in page


def test_a_video_longer_than_the_limit_does_not_open(client, mine, monkeypatch):
    monkeypatch.setenv("OFM_EDIT_MAX_INPUT_S", "2")

    page = client.get("/edit", params={"video": _clip(mine)}).text

    assert 'id="edit"' not in page
    assert "Videos up to 2 s open here: this one is 4 s" in page


def test_another_persons_video_never_opens_or_cuts(client, mine):
    theirs = assets_root_for_user(make_user("other@example.com").id)
    video = str(_video(theirs / f"{'b' * 64}__theirs.mp4"))

    assert 'id="edit"' not in client.get("/edit", params={"video": video}).text
    refused = _cut(client, video, 0, 2)
    assert refused.status_code == 400
    assert refused.json() == {"detail": "That video is gone: pick it again"}
    assert client.get("/edit/strip", params={"video": video}).status_code == 400


def test_the_strip_is_one_jpeg_made_once(client, mine):
    first = client.get("/edit/strip", params={"video": _clip(mine)})
    strips = list(
        (Path(settings.infra.uploads_root) / ".previews" / "strips").glob("*")
    )

    assert first.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert len(strips) == 1
    assert (
        client.get("/edit/strip", params={"video": _clip(mine)}).content
        == first.content
    )


def test_a_cut_is_a_job_that_makes_the_part_in_edit_staging(client, mine, monkeypatch):
    monkeypatch.setenv("OFM_EDIT_SHORT_SIDE", "240")

    queued = _cut(client, _clip(mine), 1, 3)
    state = client.get(f"/edit/cut/{queued.json()['job_id']}").json()

    assert state["status"] == "done"
    path = state["video"]["path"]
    assert Path(path).parent == (mine / ".edit")
    assert path.endswith("__clip_cut.mp4")
    width, height, seconds = _shape(path)
    assert (width, height) == (426, 240)  # short side capped, aspect kept
    assert seconds == pytest.approx(2, abs=0.1)
    assert get_job(queued.json()["job_id"]).task == "edit_cut"


def test_a_small_video_is_never_scaled_up(client, mine):
    queued = _cut(client, _clip(mine), 0, 1)

    path = client.get(f"/edit/cut/{queued.json()['job_id']}").json()["video"]["path"]
    assert _shape(path)[:2] == (640, 360)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [(1, 1.2, "Pick at least 0.5 s"), (3.9, 9, "Pick at least 0.5 s")],
)
def test_a_part_too_short_is_refused(client, mine, start, end, message):
    refused = _cut(client, _clip(mine), start, end)

    assert refused.status_code == 400
    assert refused.json() == {"detail": message}


def test_a_part_too_long_is_refused(client, mine, monkeypatch):
    monkeypatch.setenv("OFM_EDIT_MAX_PART_S", "2")

    refused = _cut(client, _clip(mine), 0, 3)

    assert refused.json() == {"detail": "Pick at most 2 s"}


@pytest.mark.parametrize("bad", ["Infinity", "NaN", "-1"])
def test_a_number_that_is_no_time_is_refused(client, mine, bad):
    body = f'{{"video": "{_clip(mine)}", "start": {bad}, "end": 2}}'

    answer = client.post(
        "/edit/cut", content=body, headers={"Content-Type": "application/json"}
    )

    assert answer.status_code == 422


def test_one_cut_in_line_per_person(client, mine):
    me = make_user()
    create_job("edit_cut", {"video": _clip(mine)}, actor=str(me.id))

    refused = _cut(client, _clip(mine), 0, 2)

    assert refused.json() == {"detail": "Your last cut is still running: wait for it"}


def test_a_failed_cut_says_one_sentence_never_ffmpegs(client, mine, monkeypatch):
    def broken(*_a, **_k):
        msg = "ffmpeg failed: /secret/path broke"
        raise RuntimeError(msg)

    monkeypatch.setattr(edit_service.trim, "cut", broken)

    queued = _cut(client, _clip(mine), 0, 2)
    state = client.get(f"/edit/cut/{queued.json()['job_id']}").json()

    assert state == {
        "status": "failed",
        "ahead": None,
        "video": None,
        "error": "Could not cut this video: try again",
    }


def test_someone_elses_cut_job_is_not_mine_to_read(client, mine):
    other = make_user("other@example.com")
    job_id = create_job("edit_cut", {}, actor=str(other.id))

    assert client.get(f"/edit/cut/{job_id}").json()["error"] == (
        "This cut is gone: cut again"
    )


def test_an_upload_is_a_video_kept_in_edit_staging(client, mine, tmp_path):
    clip = _video(tmp_path / "phone.mp4", seconds=2)

    with clip.open("rb") as f:
        answer = client.post(
            "/edit/upload", files={"file": ("phone.mp4", f, "video/mp4")}
        )

    assert answer.status_code == 200
    assert Path(answer.json()["path"]).parent == mine / ".edit"
    refused = client.post(
        "/edit/upload", files={"file": ("notes.txt", b"hi", "text/plain")}
    )
    assert refused.json() == {"detail": "Pick a video file"}


def test_an_upload_over_the_size_cap_is_refused_unsaved(client, mine, monkeypatch):
    monkeypatch.setenv("OFM_EDIT_MAX_UPLOAD_MB", "1")

    refused = client.post(
        "/edit/upload",
        files={"file": ("big.mp4", b"0" * (2 * 1024 * 1024), "video/mp4")},
    )

    assert refused.json() == {"detail": "Videos up to 1 MB open here"}
    assert not (mine / ".edit").exists()


def test_what_edit_makes_never_shows_in_the_reuse_pickers(client, mine):
    queued = _cut(client, _clip(mine), 0, 2)
    assert client.get(f"/edit/cut/{queued.json()['job_id']}").json()["status"] == "done"

    listed = client.get("/refs", params={"kind": "video", "limit": 60}).json()

    assert [f["name"] for f in listed] == ["clip.mp4"]


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_the_sweep_deletes_what_edit_made_a_day_ago_unless_a_job_needs_it(store):
    staging = store / "users" / "7" / ".edit"
    staging.mkdir(parents=True)
    old, fresh, needed = (staging / f"{n}.mp4" for n in ("old", "fresh", "needed"))
    for path in (old, fresh, needed):
        path.write_bytes(b"v")
    _age(old, 2 * 86400)
    _age(needed, 2 * 86400)
    create_job("clone", {"reference_videos": [str(needed)]})
    strips = Path(settings.infra.uploads_root) / ".previews" / "strips"
    strips.mkdir(parents=True, exist_ok=True)  # the strip test's, same worker
    old_strip = strips / "s.jpg"
    old_strip.write_bytes(b"j")
    _age(old_strip, 2 * 86400)
    left = Path(settings.infra.uploads_root) / ".edit" / "tmpabc"
    left.mkdir(parents=True, exist_ok=True)
    _age(left, 3600)

    cleanup.remove_old()

    assert not old.exists()
    assert fresh.exists()
    assert needed.exists()
    assert not old_strip.exists()
    assert not left.exists()
