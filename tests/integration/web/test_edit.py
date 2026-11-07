"""/edit (routers/video_tools/edit.py, features/edit): open a stored video,
its light copy, strip and frame times (edit_prepare), a frame grab and a cut
turned, cropped, sped and sized as asked, queued on the edit worker (inline
in the suite) and polled, the limits a request cannot get past, nothing kept
in the Reuse pickers, and the sweep that deletes what /edit made after a day."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import io
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from redis.exceptions import RedisError
from signin import make_user, sign_in

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job, get_job, list_jobs_page
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
    # Light copies, strips and work folders of this test only.
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
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


def _cut(client, video: str, start: float, end: float, **picked: object):
    body = {"video": video, "start": start, "end": end, **picked}
    return client.post("/edit/cut", json=body)


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


def test_the_strip_is_made_by_the_job_the_page_queues_never_in_the_request(
    client, mine
):
    video = _clip(mine)
    assert client.get("/edit/strip", params={"video": video}).status_code == 404

    client.get("/edit", params={"video": video})  # queues edit_prepare (run inline)
    first = client.get("/edit/strip", params={"video": video})
    strips = list(edit_service.prepared_dir().glob("*.jpg"))

    assert first.status_code == 200
    assert first.headers["content-type"] == "image/jpeg"
    assert len(strips) == 1
    assert client.get("/edit/strip", params={"video": video}).content == first.content


def test_a_cut_is_a_job_that_makes_the_part_in_edit_staging(client, mine):
    queued = _cut(client, _clip(mine), 1, 3, size="720")
    state = client.get(f"/edit/cut/{queued.json()['job_id']}").json()

    assert state["status"] == "done"
    path = state["video"]["path"]
    assert Path(path).parent == (mine / ".edit")
    assert path.endswith("__clip_cut.mp4")
    width, height, seconds = _shape(path)
    assert (width, height) == (1280, 720)  # the size asked, aspect kept
    assert seconds == pytest.approx(2, abs=0.1)
    assert get_job(queued.json()["job_id"]).task == "edit_cut"


def test_a_cut_comes_out_at_1080p_unless_another_size_is_picked(client, mine):
    queued = _cut(client, _clip(mine), 0, 1)

    path = client.get(f"/edit/cut/{queued.json()['job_id']}").json()["video"]["path"]
    assert _shape(path)[:2] == (1920, 1080)


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
    strips.mkdir(parents=True, exist_ok=True)  # the strips made before .previews/edit
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


# --- the light copy, its strip and frame times (edit_prepare) -------------------


def _prepares(video: str) -> list:
    jobs, _ = list_jobs_page({"edit_prepare"}, 0, 50)
    return [j for j in jobs if j.params.get("video") == video]


def test_opening_a_video_makes_its_light_copy_strip_and_frame_times(client, mine):
    video = _clip(mine)
    for kind in ("proxy", "frames"):
        assert client.get(f"/edit/{kind}", params={"video": video}).status_code == 404

    page = client.get("/edit", params={"video": video}).text
    state = client.get("/edit/prepared", params={"video": video}).json()
    proxy = client.get("/edit/proxy", params={"video": video})
    part = client.get(
        "/edit/proxy", params={"video": video}, headers={"Range": "bytes=0-99"}
    )
    times = client.get("/edit/frames", params={"video": video}).json()["times"]

    assert 'data-ready="true"' in page
    assert state["status"] == "done"
    assert state["frames_url"].startswith("/edit/frames?video=")
    assert proxy.headers["content-type"] == "video/mp4"
    assert part.status_code == 206
    assert len(part.content) == 100
    made = next(edit_service.prepared_dir().glob("*.mp4"))
    assert _shape(str(made))[:2] == (640, 360)  # under 540 short side: kept
    assert len(times) == 100  # 4 s at 25 fps
    assert times[0] == 0
    assert times == sorted(times)
    assert len(_prepares(video)) == 1


def test_an_odd_sized_video_gets_an_even_light_copy(client, mine, tmp_path):
    odd = mine / f"{'c' * 64}__odd.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=361x641:duration=1:rate=25",
            "-pix_fmt",
            "yuv444p",
            str(odd),
        ],
        check=True,
        capture_output=True,
    )

    client.get("/edit", params={"video": str(odd.resolve())})

    made = next(edit_service.prepared_dir().glob("*.mp4"))
    assert _shape(str(made))[:2] == (360, 640)


def test_same_named_videos_never_share_a_light_copy(client, mine):
    one = _video(mine / "a" / "clip.mp4", seconds=1)
    two = _video(mine / "b" / "clip.mp4", seconds=1, size="320x240")

    client.get("/edit", params={"video": str(one)})
    client.get("/edit", params={"video": str(two)})

    copies = sorted(
        _shape(str(p))[:2] for p in edit_service.prepared_dir().glob("*.mp4")
    )
    assert copies == [(320, 240), (640, 360)]


def test_an_upload_and_its_page_make_one_light_copy(client, mine, tmp_path):
    clip = _video(tmp_path / "phone.mp4", seconds=1)
    with clip.open("rb") as f:
        path = client.post(
            "/edit/upload", files={"file": ("phone.mp4", f, "video/mp4")}
        ).json()["path"]

    client.get("/edit", params={"video": path})

    assert len(_prepares(path)) == 1


def test_a_video_too_slow_to_copy_is_not_tried_again_on_every_open(
    client, mine, monkeypatch
):
    def too_slow(*_args, **_kwargs):
        msg = "ffmpeg timed out after 600 s"
        raise RuntimeError(msg)

    monkeypatch.setattr(edit_service.trim, "proxy", too_slow)
    video = _clip(mine)

    client.get("/edit", params={"video": video})
    page = client.get("/edit", params={"video": video}).text

    assert 'data-ready="false"' in page
    assert [j.error for j in _prepares(video)] == [
        "Could not read this video: pick another one"
    ]
    assert client.get("/edit/prepared", params={"video": video}).json() == {
        "status": "failed",
        "ahead": None,
        "proxy_url": None,
        "strip_url": None,
        "frames_url": None,
    }


def test_a_copy_that_failed_for_another_reason_is_tried_again(
    client, mine, monkeypatch
):
    def disk_full(*_args, **_kwargs):
        msg = "ffmpeg failed: No space left on device"
        raise RuntimeError(msg)

    monkeypatch.setattr(edit_service.trim, "proxy", disk_full)
    video = _clip(mine)

    client.get("/edit", params={"video": video})
    client.get("/edit", params={"video": video})

    errors = [j.error for j in _prepares(video)]
    assert errors == ["Could not make the light copy: open the video again"] * 2


def test_the_page_plays_the_original_when_the_queue_is_down(client, mine, monkeypatch):
    def down(*_args, **_kwargs):
        msg = "Connection refused"
        raise RedisError(msg)

    monkeypatch.setattr(edit_service, "start_job", down)

    page = client.get("/edit", params={"video": _clip(mine)})

    assert page.status_code == 200
    assert 'data-ready="false"' in page.text
    assert 'src="/refs/file?path=' in page.text


def test_the_sweep_deletes_light_copies_a_day_old(store):
    folder = edit_service.prepared_dir()
    folder.mkdir(parents=True, exist_ok=True)
    old, fresh = folder / "old_540p.mp4", folder / "fresh_frames.json"
    for path in (old, fresh):
        path.write_bytes(b"x")
    _age(old, 2 * 86400)

    cleanup.remove_old()

    assert not old.exists()
    assert fresh.exists()


# --- turn, mirror, crop, size, speed --------------------------------------------


def _halves(dest: Path) -> Path:
    """Left half white, right half black: a turn and a mirror show."""
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:size=640x360:duration=2:rate=25",
            "-vf",
            "drawbox=x=0:y=0:w=320:h=360:color=white:t=fill",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest.resolve()


def _frame(client, video: str, **asked: str) -> Image.Image:
    answer = client.get("/edit/frame", params={"video": video, "t": "1", **asked})
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"] == "image/jpeg"
    return Image.open(io.BytesIO(answer.content)).convert("L")


def _bright(img: Image.Image, box: tuple[float, float, float, float]) -> float:
    w, h = img.size
    part = img.crop(
        (int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h))
    )
    return sum(part.getdata()) / (part.width * part.height)


def test_a_frame_is_turned_then_mirrored_as_the_page_shows_it(client, mine):
    video = str(_halves(mine / f"{'d' * 64}__halves.mp4"))

    plain = _frame(client, video, size="720")
    mirrored = _frame(client, video, mirror="true", size="720")
    turned = _frame(client, video, turn="90", size="720")

    assert plain.size == (1280, 720)
    assert _bright(plain, (0, 0, 0.4, 1)) > 200  # white on the left
    assert _bright(mirrored, (0, 0, 0.4, 1)) < 50  # mirrored: black on the left
    assert turned.size == (720, 1280)  # a quarter turn: portrait
    assert _bright(turned, (0, 0, 1, 0.4)) > 200  # clockwise: the left went on top


def test_a_frame_is_cropped_and_sized_and_named_by_its_time(client, mine):
    answer = client.get(
        "/edit/frame",
        params={
            "video": _clip(mine),
            "t": "1.25",
            "turn": "90",
            "aspect": "1:1",
            "scale": "0.5",
            "x": "1",
            "y": "0",
            "size": "2160",
        },
    )

    assert Image.open(io.BytesIO(answer.content)).size == (2160, 2160)
    assert answer.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''clip_00m01.2s.jpg"
    )


def test_a_cut_is_turned_cropped_sped_and_sized(client, mine):
    queued = _cut(
        client,
        _clip(mine),
        0,
        4,
        turn=270,
        mirror=True,
        aspect="1:1",
        scale=0.5,
        size="720",
        speed=2,
    )

    path = client.get(f"/edit/cut/{queued.json()['job_id']}").json()["video"]["path"]
    width, height, seconds = _shape(path)
    assert (width, height) == (720, 720)
    assert seconds == pytest.approx(2, abs=0.1)


@pytest.mark.parametrize(
    "bad",
    [{"turn": 45}, {"scale": 0.05}, {"size": "480"}, {"aspect": "2:1"}, {"x": 1.5}],
)
def test_a_turn_crop_or_size_the_page_never_offers_is_refused(client, mine, bad):
    cut = _cut(client, _clip(mine), 0, 2, **bad)
    frame = client.get("/edit/frame", params={"video": _clip(mine), "t": "1", **bad})

    assert cut.status_code == 422
    assert frame.status_code == 422


def test_a_speed_the_page_never_offers_is_refused(client, mine):
    assert _cut(client, _clip(mine), 0, 2, speed=1.3).status_code == 422


def test_a_frame_of_someone_elses_video_is_refused(client, mine):
    theirs = assets_root_for_user(make_user("other@example.com").id)
    video = str(_video(theirs / f"{'b' * 64}__theirs.mp4", seconds=1))

    refused = client.get("/edit/frame", params={"video": video, "t": "0"})

    assert refused.status_code == 400
    assert refused.json() == {"detail": "That video is gone: pick it again"}
    for kind in ("prepared", "proxy", "frames"):
        assert client.get(f"/edit/{kind}", params={"video": video}).status_code == 400


def test_frame_grabs_wait_their_turn_briefly_then_say_busy(client, mine, monkeypatch):
    monkeypatch.setenv("OFM_EDIT_FRAME_WAIT_S", "0")
    edit_service._grabbing.cache_clear()
    turns = edit_service._grabbing()
    for _ in range(settings.web.edit_frame_parallel):
        turns.acquire()
    try:
        busy = client.get("/edit/frame", params={"video": _clip(mine), "t": "0"})
    finally:
        for _ in range(settings.web.edit_frame_parallel):
            turns.release()
        edit_service._grabbing.cache_clear()

    assert busy.status_code == 400
    assert busy.json() == {"detail": "Busy saving frames: try again"}
