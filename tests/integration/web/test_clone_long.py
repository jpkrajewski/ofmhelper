"""/clone-long: one image + one long video through Wan 3.0 in pieces of at
most 15 s, joined into one video of the source's shape. Real upload,
ffprobe, ffmpeg, runner and recovery; only the provider's calls are faked
(the suite's RQ runs jobs inline)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import contextvars
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from rq.timeouts import JobTimeoutException
from signin import sign_in

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.base import TaskState
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderGenerationError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.aigenproviders.kaiai.provider import KieProvider
from ofmhelpers.config import settings
from ofmhelpers.web import recovery
from ofmhelpers.web.clone_long import (
    TARGETS,
    LongClone,
    LongCloneParams,
    Shape,
    service,
)
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.registry import JOB_TIMEOUTS, RESUMABLE
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.stores.jobs import fail_job, get_job, list_jobs

NEW = json.dumps([{"kind": "new"}])
PROVIDERS = {"kie": KieProvider, "higgsfield": HiggsfieldProvider}


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(task_helper_uploads, "ASSETS_ROOT", tmp_path / "assets")
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("OFM_CLONE_CREDITS_PER_VIDEO_S", '{"720P": 32.78}')
    monkeypatch.setenv("OFM_KIE_USD_PER_CREDIT", "0.005")


@pytest.fixture
def client():
    c = TestClient(app)
    sign_in(c)
    yield c
    c.close()


def _clip(dest: Path, seconds: float, size: str = "64x36") -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate=10:duration={seconds}",
            "-pix_fmt",
            "yuv420p",
            str(dest),
        ],
        check=True,
    )
    return dest


def _size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    width, height = out.strip().split(",")
    return int(width), int(height)


def _provider(monkeypatch, provider_cls, generated: Path, sent: list, status=None):
    """The provider's single calls answered locally; every result downloads
    as `generated` (a real clip, so the pieces can be joined)."""

    def submit(_self, spec, payload, idempotency_key):
        sent.append({"key": idempotency_key, **payload})
        return f"t{len(sent)}"

    def download(_self, url, dest):
        dest.write_bytes(generated.read_bytes())
        return dest

    monkeypatch.setattr(provider_cls, "upload", lambda _self, p: f"https://up/{p.name}")
    monkeypatch.setattr(provider_cls, "submit", submit)
    monkeypatch.setattr(
        provider_cls,
        "status",
        status
        or (
            lambda _self, tid: TaskState(state="done", urls=[f"https://res/{tid}.mp4"])
        ),
    )
    monkeypatch.setattr(provider_cls, "download", download)


def _post(client, video: Path | None, data=None, images=1):
    files = [("image", (f"face{i}.png", b"img", "image/png")) for i in range(images)]
    if video is not None:
        files.append(("video", (video.name, video.read_bytes(), "video/mp4")))
    return client.post(
        "/clone-long/run",
        data={
            "api_key": "secret-key-123",
            "provider": "kie",
            "image_manifest": json.dumps([{"kind": "new"}] * images),
            "video_manifest": NEW if video is not None else "[]",
            **(data or {}),
        },
        files=files,
    )


# --- the page ----------------------------------------------------------------


def test_the_page_offers_one_image_one_video_and_the_prices(client):
    html = client.get("/clone-long").text

    assert 'href="/clone-long"' in html  # in the nav
    assert 'data-field="image" data-kind="image" data-max="1"' in html
    assert 'data-field="video" data-kind="video" data-max="1"' in html
    assert 'data-prefix="/clone-long"' in html
    assert "No long clones yet" in html
    assert "export" not in html.lower().split("<form", 1)[1].split("</form>", 1)[0]
    rates = json.loads(
        html.split("data-rates='", 1)[1].split("'", 1)[0].replace("&#34;", '"')
    )
    assert set(rates) == set(TARGETS)
    assert rates["kie"] == {"credits_per_s": {"720P": 32.78}, "usd_per_credit": 0.005}


# --- a run end to end ----------------------------------------------------------


@pytest.mark.slow
@pytest.mark.parametrize("provider", list(TARGETS))
def test_a_16_9_video_is_generated_in_pieces_and_comes_out_16_9(
    client, tmp_path, monkeypatch, provider
):
    sent = []
    generated = _clip(tmp_path / "g.mp4", 2, size="1280x720")
    _provider(monkeypatch, PROVIDERS[provider], generated, sent)
    target = TARGETS[provider]

    r = _post(
        client, _clip(tmp_path / "long.mp4", 32, "1280x720"), {"provider": provider}
    )

    assert r.status_code == 200, r.text
    job = get_job(r.json()["job_id"])
    assert job.status == "done", job.error
    # 3 pieces (11 + 11 + 10 s), each its own request, same image and prompt.
    assert sorted(p["duration"] for p in sent) == [10, 11, 11]
    assert {p["key"] for p in sent} == {job.id, f"{job.id}:1", f"{job.id}:2"}
    for payload in sent:
        assert payload["aspect_ratio"] == "16:9"
        assert payload[target.audio_field] is False
        assert payload[target.image_field][0].endswith("face0.png")
        assert payload["prompt"]
    assert {p[target.video_field][0].rsplit("/", 1)[-1] for p in sent} == {
        "piece_0.mp4",
        "piece_1.mp4",
        "piece_2.mp4",
    }
    # One video, the three generations joined, the source's shape.
    [result] = job.result_files()
    assert result.name == f"clone_long_{job.id}.mp4"
    assert _size(Path(result.path)) == (1280, 720)
    assert service.VideoTools(20).length(Path(result.path)) == pytest.approx(6, abs=0.3)
    assert not (service.work_dir() / job.id).exists()
    assert tasks.recall(job.id) is None
    # The pieces are no results of their own: neither in the AI output
    # folder nor in the user's store -- only the joined video is.
    assert [p.name for p in (tmp_path / "out").rglob("*.mp4")] == [result.name]
    stored = {p.name.split("__", 1)[-1] for p in (tmp_path / "assets").rglob("*.mp4")}
    assert stored == {"long.mp4", result.name}


@pytest.mark.slow
def test_a_4_5_video_is_made_at_3_4_and_comes_out_4_5(client, tmp_path, monkeypatch):
    """Wan 3.0 has no 4:5: every piece is asked at its nearest (3:4) and the
    joined video is fitted back to the source's exact 4:5."""
    sent = []
    generated = _clip(tmp_path / "g.mp4", 2, size="768x1024")
    _provider(monkeypatch, KieProvider, generated, sent)

    r = _post(client, _clip(tmp_path / "portrait.mp4", 20, "1080x1350"))

    job = get_job(r.json()["job_id"])
    assert job.status == "done", job.error
    assert (job.params["width"], job.params["height"]) == (1080, 1350)
    assert {p["aspect_ratio"] for p in sent} == {"3:4"}
    [result] = job.result_files()
    assert _size(Path(result.path)) == (818, 1024)


@pytest.mark.slow
def test_the_price_is_the_servers_and_the_key_is_never_stored(
    client, tmp_path, monkeypatch
):
    sent = []
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), sent)

    r = _post(
        client,
        _clip(tmp_path / "long.mp4", 32),
        {"credits": "1", "usd": "0.01", "pieces": "1"},  # whatever a page sends
    )

    job = get_job(r.json()["job_id"])
    assert job.params["pieces"] == 3
    assert job.params["credits"] == pytest.approx(32 * 32.78, abs=0.2)
    assert job.params["usd"] == pytest.approx(5.24, abs=0.01)
    assert "secret-key-123" not in json.dumps(job.params)
    page = client.get("/clone-long").text
    assert "Wan 3.0 · 3 pieces · $5.24" in page


# --- refused before anything is paid ------------------------------------------


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        ({"api_key": ""}, "API key is required"),
        ({"provider": "openai"}, "That provider cannot clone"),
        ({"resolution": "4K"}, "Unknown resolution"),
    ],
)
def test_a_bad_form_queues_nothing(client, tmp_path, change, detail):
    before = len(list_jobs())
    r = _post(client, _clip(tmp_path / "v.mp4", 3), change)
    assert (r.status_code, r.json()["detail"]) == (400, detail)
    assert len(list_jobs()) == before


def test_exactly_one_image_and_one_video(client, tmp_path):
    before = len(list_jobs())
    two = _post(client, _clip(tmp_path / "v.mp4", 3), images=2)
    none = _post(client, None)
    assert (two.status_code, two.json()["detail"]) == (400, "Pick one subject image")
    assert (none.status_code, none.json()["detail"]) == (400, "Pick one video")
    assert len(list_jobs()) == before


def test_a_video_over_the_longest_queues_nothing(client, tmp_path, monkeypatch):
    monkeypatch.setenv("OFM_CLONE_LONG_MAX_S", "20")
    before = len(list_jobs())
    r = _post(client, _clip(tmp_path / "v.mp4", 25))
    assert (r.status_code, r.json()["detail"]) == (
        400,
        "The video is 25 s; the longest is 20 s",
    )
    assert len(list_jobs()) == before


def test_an_unreadable_video_queues_nothing(client, tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    before = len(list_jobs())
    r = _post(client, broken)
    assert (r.status_code, r.json()["detail"]) == (
        400,
        "Could not read the video's length",
    )
    assert len(list_jobs()) == before


def test_another_user_never_sees_a_run(client, tmp_path, monkeypatch):
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), [])
    job_id = _post(client, _clip(tmp_path / "v.mp4", 3)).json()["job_id"]
    other = TestClient(app)
    sign_in(other, "someone.else@example.com")
    assert other.get(f"/clone-long/jobs/{job_id}/status").status_code == 404
    assert other.get(f"/clone-long/files/{job_id}/0").status_code == 404
    assert job_id not in other.get("/clone-long").text
    other.close()


# --- paid pieces are never paid twice ----------------------------------------


def _params(tmp_path, seconds=32.0) -> LongCloneParams:
    face = tmp_path / "face.png"
    face.write_bytes(b"img")
    return LongClone.prepare(
        provider="kie",
        prompt="p",
        resolution="720P",
        image=str(face),
        video=str(_clip(tmp_path / "long.mp4", seconds)),
        seconds=seconds,
        shape=Shape(width=64, height=36),
    ).params


@pytest.mark.slow
def test_a_rerun_resumes_every_piece_and_pays_for_none_again(tmp_path, monkeypatch):
    """The join dies (a deploy, a full disk); the re-run resumes the three
    remembered requests: nothing is submitted again."""
    sent = []
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), sent)
    params = _params(tmp_path)
    real_join = service.VideoTools.join
    calls = {"n": 0}

    def join_dies_once(self, parts, dest, shape, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            msg = "killed"
            raise RuntimeError(msg)
        return real_join(self, parts, dest, shape, **kwargs)

    monkeypatch.setattr(service.VideoTools, "join", join_dies_once)
    clone = LongClone(params)

    with pytest.raises(RuntimeError, match="killed"):
        clone.run("rerun01", "k", str(tmp_path / "assets"))
    assert len(sent) == 3
    assert {t.part for t in tasks.remembered() if t.job_id == "rerun01"} == {0, 1, 2}

    [result] = clone.run("rerun01", "k", str(tmp_path / "assets"))

    assert len(sent) == 3
    assert Path(result.path).is_file()
    tasks.forget("rerun01")


@pytest.mark.slow
def test_a_refused_piece_fails_the_run_and_leaves_nothing_to_retry(
    tmp_path, monkeypatch
):
    def status(_self, tid):
        if tid == "t2":
            return TaskState(state="failed", error="flagged as sensitive")
        return TaskState(state="done", urls=[f"https://res/{tid}.mp4"])

    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), [], status)

    with pytest.raises(Exception, match="flagged as sensitive"):
        LongClone(_params(tmp_path)).run("refused1", "k", str(tmp_path / "assets"))

    assert [t for t in tasks.remembered() if t.job_id == "refused1"] == []


@pytest.mark.slow
def test_a_refused_piece_never_leaves_a_piece_remembered_20_times(
    tmp_path, monkeypatch
):
    """The pieces run in parallel: whatever order they end in, a refusal
    leaves nothing remembered (else recovery would pay for it again)."""
    for attempt in range(20):

        def status(_self, tid):
            if tid == "t1":
                return TaskState(state="failed", error="flagged")
            return TaskState(state="done", urls=[f"https://res/{tid}.mp4"])

        sent = []
        _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 1), sent, status)
        job = f"race{attempt}"
        with pytest.raises(Exception, match="flagged"):
            LongClone(_params(tmp_path, 46)).run(job, "k", str(tmp_path / "a"))
        assert [t for t in tasks.remembered() if t.job_id == job] == []


@pytest.mark.slow
def test_a_refused_piece_starts_no_further_piece(tmp_path, monkeypatch):
    """One at a time, the first refused: the other three are never sent
    (each would be paid for a run that fails anyway)."""
    monkeypatch.setenv("OFM_CLONE_LONG_PARALLEL", "1")

    def status(_self, tid):
        if tid == "t1":
            return TaskState(state="failed", error="flagged as sensitive")
        return TaskState(state="done", urls=[f"https://res/{tid}.mp4"])

    sent = []
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 1), sent, status)

    with pytest.raises(ProviderGenerationError, match="flagged as sensitive"):
        LongClone(_params(tmp_path, 46)).run("stop01", "k", str(tmp_path / "a"))

    assert len(sent) == 1
    assert [t for t in tasks.remembered() if t.job_id == "stop01"] == []
    assert not (service.work_dir() / "stop01").exists()


# --- chained pieces (OFM_CLONE_LONG_CHAIN) -------------------------------------


@pytest.mark.parametrize("provider", list(TARGETS))
@pytest.mark.parametrize("audio", [False, True])
def test_a_pieces_request_follows_the_audio_switch(
    tmp_path, monkeypatch, provider, audio
):
    monkeypatch.setenv("OFM_CLONE_LONG_AUDIO", "1" if audio else "0")
    target = TARGETS[provider]

    values = target.values(_params(tmp_path, 10), 10, "piece.mp4")

    assert values[target.audio_field] is audio


@pytest.mark.parametrize("provider", list(TARGETS))
def test_a_chained_pieces_request_starts_from_the_frame_given(tmp_path, provider):
    target = TARGETS[provider]
    params = _params(tmp_path, 10)

    values = target.values(params, 10, "piece.mp4", "chain_0.png")

    assert values[target.image_field] == [params.image, "chain_0.png"]
    assert values["prompt"] == "p Start video from @Image2"


@pytest.mark.slow
def test_chained_pieces_go_in_order_each_from_the_last_frame_before_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OFM_CLONE_LONG_CHAIN", "1")
    sent = []
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), sent)
    images = TARGETS["kie"].image_field

    [result] = LongClone(_params(tmp_path)).run("chain01", "k", str(tmp_path / "a"))

    assert [p["key"] for p in sent] == ["chain01", "chain01:1", "chain01:2"]
    assert [p[images] for p in sent] == [
        ["https://up/face.png"],
        ["https://up/face.png", "https://up/chain_0.png"],
        ["https://up/face.png", "https://up/chain_1.png"],
    ]
    assert [p["prompt"] for p in sent] == ["p"] + ["p Start video from @Image2"] * 2
    assert service.VideoTools(20).length(Path(result.path)) == pytest.approx(6, abs=0.3)
    assert not (service.work_dir() / "chain01").exists()
    tasks.forget("chain01")


@pytest.mark.slow
def test_a_refused_chained_piece_starts_no_further_piece(tmp_path, monkeypatch):
    monkeypatch.setenv("OFM_CLONE_LONG_CHAIN", "1")

    def status(_self, tid):
        if tid == "t2":
            return TaskState(state="failed", error="flagged as sensitive")
        return TaskState(state="done", urls=[f"https://res/{tid}.mp4"])

    sent = []
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 1), sent, status)

    with pytest.raises(ProviderGenerationError, match="flagged as sensitive"):
        LongClone(_params(tmp_path, 46)).run("chain02", "k", str(tmp_path / "a"))

    assert len(sent) == 2
    assert [t for t in tasks.remembered() if t.job_id == "chain02"] == []
    assert not (service.work_dir() / "chain02").exists()


@pytest.mark.slow
def test_a_chained_rerun_takes_the_saved_pieces_and_pays_for_none_again(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OFM_CLONE_LONG_CHAIN", "1")
    sent = []
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), sent)
    real_join = service.VideoTools.join
    calls = {"n": 0}

    def join_dies_once(self, parts, dest, shape, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            msg = "killed"
            raise RuntimeError(msg)
        return real_join(self, parts, dest, shape, **kwargs)

    monkeypatch.setattr(service.VideoTools, "join", join_dies_once)
    clone = LongClone(_params(tmp_path))

    with pytest.raises(RuntimeError, match="killed"):
        clone.run("chain03", "k", str(tmp_path / "a"))
    [result] = clone.run("chain03", "k", str(tmp_path / "a"))

    assert len(sent) == 3
    assert Path(result.path).is_file()
    tasks.forget("chain03")


@pytest.mark.slow
def test_a_wrong_key_fails_the_run_and_leaves_no_cut_pieces(
    client, tmp_path, monkeypatch
):
    """Refused at the first upload: nothing was made, so nothing can resume
    -- the cut pieces go with the run."""
    sent = []
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), sent)

    def upload(_self, _path):
        provider = "kie.ai"
        raise ProviderAuthError(provider)

    monkeypatch.setattr(KieProvider, "upload", upload)

    job_id = _post(client, _clip(tmp_path / "long.mp4", 32)).json()["job_id"]

    job = get_job(job_id)
    assert (job.status, job.error) == ("failed", "kie.ai: wrong API key")
    assert sent == []
    assert not (service.work_dir() / job_id).exists()


@pytest.mark.slow
def test_a_piece_that_fails_to_download_is_finished_by_recovery_unpaid(
    client, tmp_path, monkeypatch
):
    """The provider made all three; one download fails every try. The run
    fails with every request kept, and recovery finishes it: nothing sent
    again, only the missing piece downloaded again."""
    sent = []
    generated = _clip(tmp_path / "g.mp4", 2)
    _provider(monkeypatch, KieProvider, generated, sent)
    downloads = []
    # The runner's own tries (review #11) all fail on the first run.
    tries = 1 + len(settings.aigen.download_retry_waits_s)

    def download(_self, url, dest):
        downloads.append(url)
        if url.endswith("/t2.mp4") and downloads.count(url) <= tries:
            msg = "kie.ai: could not download"
            raise ProviderUnavailableError(msg)
        dest.write_bytes(generated.read_bytes())
        return dest

    monkeypatch.setattr(KieProvider, "download", download)

    job_id = _post(client, _clip(tmp_path / "long.mp4", 32)).json()["job_id"]

    assert get_job(job_id).status == "failed"
    assert {t.part for t in tasks.remembered() if t.job_id == job_id} == {0, 1, 2}

    assert recovery.run_recovery_once()[job_id] == "rerun"

    job = get_job(job_id)
    assert job.status == "done", job.error
    assert len(sent) == 3
    assert sorted(downloads) == sorted(
        [*(f"https://res/t{i}.mp4" for i in (1, 2, 3)), *["https://res/t2.mp4"] * tries]
    )
    assert tasks.recall(job_id) is None


@pytest.mark.slow
def test_rqs_time_limit_starts_no_further_piece_and_keeps_the_paid_ones(
    tmp_path, monkeypatch
):
    """RQ ends the job (its time limit) while the first of three pieces
    generates: the run ends at once, the other two never start, the first
    still finishes and is saved, its request kept for recovery."""
    monkeypatch.setenv("OFM_CLONE_LONG_PARALLEL", "1")
    sent = []
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), sent)
    quick_submit = KieProvider.submit
    started, release = threading.Event(), threading.Event()

    def slow_submit(self, spec, payload, idempotency_key):
        started.set()
        release.wait(10)
        return quick_submit(self, spec, payload, idempotency_key)

    monkeypatch.setattr(KieProvider, "submit", slow_submit)

    def limit_hits(_futures):
        started.wait(10)
        msg = "Task exceeded maximum timeout value (1 sec)"
        raise JobTimeoutException(msg)

    monkeypatch.setattr(service, "wait", limit_hits)

    with pytest.raises(JobTimeoutException):
        LongClone(_params(tmp_path)).run("limit01", "k", str(tmp_path / "a"))
    release.set()
    saved = service.work_dir() / "limit01" / "result_0.mp4"
    deadline = time.monotonic() + 20
    while not saved.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)

    assert saved.is_file()
    assert (service.work_dir() / "limit01" / "piece_0.mp4").is_file()
    assert len(sent) == 1
    assert {t.part for t in tasks.remembered() if t.job_id == "limit01"} == {0}
    tasks.forget("limit01")


@pytest.mark.slow
def test_every_piece_runs_in_the_jobs_context(tmp_path, monkeypatch):
    """The pieces' threads carry the job's context: their spans and logs
    are in the job's trace."""
    job = contextvars.ContextVar("job")
    seen = []
    sent = []
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), sent)
    quick_submit = KieProvider.submit

    def submit(self, spec, payload, idempotency_key):
        seen.append(job.get(None))
        return quick_submit(self, spec, payload, idempotency_key)

    monkeypatch.setattr(KieProvider, "submit", submit)
    job.set("job ctx01")

    LongClone(_params(tmp_path)).run("ctx01", "k", str(tmp_path / "a"))

    assert seen == ["job ctx01"] * 3
    tasks.forget("ctx01")


@pytest.mark.slow
def test_a_big_videos_pieces_go_up_at_most_the_piece_size(
    client, tmp_path, monkeypatch
):
    """A 4K phone video's pieces are cut at most 1080 on the short side
    (here 18 on a 64x36 source): the uploads are that size."""
    monkeypatch.setenv("OFM_CLONE_LONG_PIECE_MAX_SIDE", "18")
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), [])
    pieces = []

    def upload(_self, path):
        if path.name.startswith("piece_"):
            pieces.append(_size(path))
        return f"https://up/{path.name}"

    monkeypatch.setattr(KieProvider, "upload", upload)

    job_id = _post(client, _clip(tmp_path / "long.mp4", 20)).json()["job_id"]

    job = get_job(job_id)
    assert job.status == "done", job.error
    assert pieces == [(32, 18), (32, 18)]
    assert (job.params["width"], job.params["height"]) == (64, 36)


@pytest.mark.slow
def test_a_stale_work_folder_is_dropped_by_the_next_run(tmp_path, monkeypatch):
    """A cut-off run nobody resumed leaves its folder; the next run drops it
    once its requests can no longer resume, never a recent one."""
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), [])
    stale = service.work_dir() / "gone01"
    stale.mkdir(parents=True)
    (stale / "piece_0.mp4").write_bytes(b"cut")
    recent = service.work_dir() / "live01"
    recent.mkdir()
    old = time.time() - 49 * 3600
    os.utime(stale, (old, old))

    LongClone(_params(tmp_path, 10)).run("prune01", "k", str(tmp_path / "a"))

    assert not stale.exists()
    assert recent.is_dir()
    tasks.forget("prune01")


def test_one_piece_says_piece_on_its_card(client, tmp_path, monkeypatch):
    _provider(monkeypatch, KieProvider, _clip(tmp_path / "g.mp4", 2), [])
    job_id = _post(client, _clip(tmp_path / "v.mp4", 3)).json()["job_id"]

    page = client.get("/clone-long").text

    assert get_job(job_id).status == "done"
    assert "Wan 3.0 · 1 piece · $" in page
    assert "1 pieces" not in page


# --- time limits and recovery ---------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (10, 2700 + 0 + 2 * 900),  # one piece, one round
        (71, 2700 + 0 + 2 * 900),  # 5 pieces, one round of 5
        (180, 2700 + 2 * 1800 + 4 * 900),  # 12 pieces, three rounds
    ],
)
def test_the_time_limit_grows_with_the_rounds_of_pieces(
    tmp_path, monkeypatch, seconds, expected
):
    monkeypatch.setenv("OFM_RQ_JOB_TIMEOUT_S", "2700")
    monkeypatch.setenv("OFM_AIGEN_VIDEO_POLL_TIMEOUT_S", "1800")
    monkeypatch.setenv("OFM_CLONE_LONG_PARALLEL", "5")
    monkeypatch.setenv("OFM_CLONE_LONG_FFMPEG_TIMEOUT_S", "900")
    params = LongCloneParams(
        provider="kie",
        model=TARGETS["kie"].model,
        prompt="p",
        resolution="720P",
        image="/x/f.png",
        video="/x/v.mp4",
        width=1920,
        height=1080,
        seconds=seconds,
        pieces=0,
        credits=0,
        usd=0,
    )
    assert LongClone(params).time_limit() == expected
    assert JOB_TIMEOUTS["clone_long"](params.model_dump()) == expected


def test_recovery_reruns_a_cut_off_run_with_its_own_time_limit(tmp_path, monkeypatch):
    """A run killed mid-way (a deploy, RQ's limit) with every piece done
    is re-run once, through clone-long, with its own limit."""
    params = _params(tmp_path).model_dump()
    from ofmhelpers.web.stores.jobs import create_job

    job_id = create_job("clone_long", params, actor="1")
    fail_job(job_id, "Task exceeded maximum timeout value")
    for part in range(3):
        tasks.remember(
            tasks.ProviderTask(
                job_id=job_id,
                part=part,
                provider="kie",
                model=TARGETS["kie"].model,
                task_id=f"t{part}",
                api_key="k",
            )
        )
    monkeypatch.setattr(
        KieProvider,
        "status",
        lambda _self, tid: TaskState(state="done", urls=["https://res/x.mp4"]),
    )
    queued = []
    monkeypatch.setattr(
        recovery,
        "enqueue_to",
        lambda _queue, *args, job_timeout=None: queued.append((args, job_timeout)),
    )

    assert recovery.run_recovery_once()[job_id] == "rerun"

    [(args, limit)] = queued
    assert args[2] is RESUMABLE["clone_long"]
    assert limit == LongClone(LongCloneParams(**params)).time_limit()
    tasks.forget(job_id)
