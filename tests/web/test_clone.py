"""/clone: subject images + N videos -> one batch of Wan 3.0 runs, grouped on the page."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import html as html_lib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import ofmhelpers.exports as exports_module
from ofmhelpers.aigenproviders.base import TaskState
from ofmhelpers.aigenproviders.errors import ProviderUnavailableError
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.aigenproviders.kaiai.provider import KieProvider
from ofmhelpers.utils.video_probe import duration_s
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.generation import clone
from ofmhelpers.web.routers.task_helpers import uploads as task_helper_uploads
from ofmhelpers.web.schemas import CloneBatch, Job, ResultFile
from ofmhelpers.web.stores.jobs import get_job, list_jobs
from ofmhelpers.web.templates_config import render_partial

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
    """Video "a.mp4" is 2.6 s long, "b.mp4" 3.6 s, and so on; no provider."""
    monkeypatch.setattr(
        clone,
        "duration_s",
        lambda path, _t: "abcdefghijklmnopqrstuvwxyz".index(Path(path).name[-5]) + 2.6,
    )
    monkeypatch.setattr(clone, "run_ai", lambda *_a, **_k: [])


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
    for target in clone.CLONE_TARGETS.values():
        assert clone.clip_duration(target.spec, seconds) == expected


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


@pytest.mark.usefixtures("fake_runs")
@pytest.mark.parametrize("provider", list(clone.CLONE_TARGETS))
def test_a_video_over_15_s_queues_nothing_on_any_provider(client, provider):
    before = len(list_jobs())

    r = _post(client, ["a.mp4", "p.mp4"], {"provider": provider})  # p: 17.6 s

    assert r.status_code == 400
    assert r.json()["detail"] == "p.mp4 is 17.6 s; Wan 3.0 takes at most 15 s"
    assert len(list_jobs()) == before


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


def test_batch_whose_subject_images_are_gone_draws_no_broken_thumb(tmp_path):
    """A batch kept after its images were deleted (file manager, or a
    database copied from another server) shows a blank tile, never a
    /refs/thumb that 404s."""
    kept = tmp_path / "kept.png"
    kept.write_bytes(b"png")
    job = _job("z1", "b", 0, 1.0)
    job.params["reference_images"] = [str(tmp_path / "gone.png"), str(kept)]
    [batch] = _batches([job], page_full=False)
    assert batch.cover == str(kept)

    job.params["reference_images"] = [str(tmp_path / "gone.png")]
    [batch] = _batches([job], page_full=False)
    assert batch.cover is None
    html = render_partial("_clone_batch.html", batch=batch)
    assert "/refs/thumb" not in html
    assert "no longer stored" in html


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


def _stub(provider_cls, monkeypatch, sent):
    """The provider's single calls answered locally; its download fails, so
    each job ends on the hosted copy (no file needed)."""

    def submit(_self, spec, payload, idempotency_key):
        sent.append((spec.model_id, payload))
        return f"t{len(sent)}"

    def download(_self, url, dest):
        msg = "no download in tests"
        raise ProviderUnavailableError(msg)

    monkeypatch.setattr(provider_cls, "upload", lambda _self, p: f"https://up/{p.name}")
    monkeypatch.setattr(provider_cls, "submit", submit)
    monkeypatch.setattr(
        provider_cls,
        "status",
        lambda _self, tid: TaskState(state="done", urls=[f"https://res/{tid}.mp4"]),
    )
    monkeypatch.setattr(provider_cls, "download", download)


def _post_clips(client, clips, data=None):
    return client.post(
        "/clone/run",
        data={
            "api_key": "k",
            "image_manifest": NEW,
            "videos_manifest": _new(len(clips)),
            **(data or {}),
        },
        files=[
            ("image", ("face.png", b"img", "image/png")),
            *[("videos", (c.name, c.read_bytes(), "video/mp4")) for c in clips],
        ],
    )


@needs_ffmpeg
def test_run_sends_kie_one_wan3_task_per_real_video(client, tmp_path, monkeypatch):
    """Real upload, real ffprobe, real runner -- only kie.ai's calls are
    stubbed, so this is exactly the payload a paid run would send."""
    sent = []
    _stub(KieProvider, monkeypatch, sent)
    clips = [_clip(tmp_path / "a.mp4", 3), _clip(tmp_path / "b.mp4", 5)]

    r = _post_clips(client, clips, {"provider": "kie"})

    assert r.status_code == 200, r.text
    assert [model for model, _ in sent] == ["wan/3-0-video", "wan/3-0-video"]
    for (_model, payload), name, seconds in zip(sent, "ab", [3, 5], strict=True):
        assert payload["prompt"] == clone.DEFAULT_PROMPT
        assert payload["duration"] == seconds
        assert payload["resolution"] == "720P"
        assert payload["nsfw_checker"] is False
        assert payload["reference_image_urls"][0].endswith("face.png")
        assert payload["reference_video_urls"][0].endswith(f"{name}.mp4")
    statuses = [get_job(j).status for j in r.json()["job_ids"]]
    assert statuses == ["done", "done"]


@needs_ffmpeg
def test_run_on_higgsfield_sends_its_own_field_names(client, tmp_path, monkeypatch):
    sent = []
    _stub(HiggsfieldProvider, monkeypatch, sent)

    r = _post_clips(
        client,
        [_clip(tmp_path / "a.mp4", 3)],
        {"provider": "higgsfield", "audio": "true"},
    )

    assert r.status_code == 200, r.text
    [(model, payload)] = sent
    assert model == "alibaba/wan-3.0/reference-to-video"
    assert payload["resolution"] == "720p", "Higgsfield spells it lower-case"
    assert payload["generate_audio"] is True
    assert payload["image_urls"][0].endswith("face.png")
    assert payload["video_urls"][0].endswith("a.mp4")
    job = get_job(r.json()["job_ids"][0])
    assert (job.params["provider"], job.params["resolution"]) == ("higgsfield", "720P")


@pytest.mark.usefixtures("fake_runs")
def test_run_rejects_a_provider_that_cannot_clone(client):
    assert _post(client, ["a.mp4"], {"provider": "fake"}).status_code == 400


@pytest.mark.usefixtures("fake_runs")
def test_run_checks_the_models_limits_before_queuing(client, monkeypatch):
    """A batch over the picked model's own limits (its schema) fails the
    submit up front, nothing paid. Seedance 2.0 takes at most 9 images."""
    monkeypatch.setitem(
        clone.CLONE_TARGETS,
        "higgsfield",
        clone.CloneTarget(
            model="higgsfield:bytedance/seedance-2.0/reference-to-video",
            images="image_urls",
            video="video_urls",
            audio="generate_audio",
        ),
    )
    files = [("image", (f"{i}.png", bytes([i]), "image/png")) for i in range(10)]
    files.append(("videos", ("a.mp4", b"a", "video/mp4")))
    before = len(list_jobs())

    r = client.post(
        "/clone/run",
        data={
            "api_key": "k",
            "provider": "higgsfield",
            "image_manifest": _new(10),
            "videos_manifest": NEW,
        },
        files=files,
    )

    assert r.status_code == 400
    assert "image_urls" in r.json()["detail"]
    assert len(list_jobs()) == before


def test_page_offers_the_providers_that_can_clone(client):
    html = client.get("/clone").text

    assert '<option value="kie"' in html
    assert '<option value="higgsfield"' in html
    assert '<option value="fake"' not in html


def _run_clone(assets_root, **choice):
    return clone._run_clone(
        job_id="j",
        api_key="k",
        assets_root=assets_root,
        provider="kie",
        prompt="p",
        resolution="720P",
        duration=5,
        reference_images=["/x/face.png"],
        reference_videos=["/x/src.mp4"],
        clone_batch="b",
        clone_index=0,
        **choice,
    )


def _generated(monkeypatch, result):
    monkeypatch.setattr(clone, "run_ai", lambda *_a, **_k: result)


def test_export_is_one_dropdown_with_capcut_selected(client):
    html = client.get("/clone").text

    assert '<select id="clone-export" name="export"' in html
    assert '<option value="capcut" selected>CapCut export</option>' in html
    assert '<option value="original" >Original</option>' in html
    assert '<option value="iphone17" >iPhone 17 Pro Max</option>' in html
    assert '<input type="checkbox" name="mirror" value="true" >' in html
    assert 'name="capcut"' not in html


@pytest.mark.usefixtures("fake_runs")
def test_export_choice_is_stored_and_recreated(client):
    picked = get_job(
        _post(client, ["a.mp4"], {"export": "iphone17", "mirror": "true"}).json()[
            "job_ids"
        ][0]
    )
    default = get_job(_post(client, ["a.mp4"]).json()["job_ids"][0])

    assert (picked.params["export"], picked.params["mirror"]) == ("iphone17", True)
    assert (default.params["export"], default.params["mirror"]) == ("capcut", False)
    [batch] = _batches([picked], page_full=False)
    recreated = json.loads(batch.recreate_json)
    assert (recreated["export"], recreated["mirror"]) == ("iphone17", True)


@pytest.mark.usefixtures("fake_runs")
def test_unknown_export_is_rejected_before_anything_runs(client):
    before = len(list_jobs())

    r = _post(client, ["a.mp4"], {"export": "nokia3310"})

    assert r.status_code == 400
    assert len(list_jobs()) == before


def test_original_export_returns_the_generation_untouched(monkeypatch):
    result = [ResultFile(name="v.mp4", path="/x/v.mp4", remote_url="https://r/v")]
    _generated(monkeypatch, result)

    assert _run_clone("/x", export="original") == result


def test_export_keeps_a_remote_only_result(monkeypatch):
    result = [ResultFile(name="v.mp4", path=None, remote_url="https://r/v")]
    _generated(monkeypatch, result)

    assert _run_clone("/x", export="capcut") == result


@needs_ffmpeg
def test_capcut_export_serves_the_capcut_copy(tmp_path, monkeypatch):
    src = _clip(tmp_path / "gen.mp4", 2)
    _generated(
        monkeypatch, [ResultFile(name=src.name, path=str(src), remote_url="https://r")]
    )
    assets = tmp_path / "assets"

    [result] = _run_clone(str(assets), export="capcut")

    out = Path(result.path)
    assert result == ResultFile(name="gen_capcut.mp4", path=str(out), remote_url=None)
    assert out.exists()
    assert src.exists()  # the original stays, and stays registered elsewhere
    assert [p.name.split("__", 1)[1] for p in assets.iterdir()] == [out.name]


def test_device_export_serves_the_device_copy(tmp_path, monkeypatch):
    src = tmp_path / "gen.mp4"
    src.write_bytes(b"video")
    _generated(
        monkeypatch, [ResultFile(name=src.name, path=str(src), remote_url="https://r")]
    )
    monkeypatch.setattr(
        exports_module, "record", lambda s, d, *_a: d.write_bytes(s.read_bytes())
    )

    [result] = _run_clone(str(tmp_path / "assets"), export="iphone17")

    out = tmp_path / "gen_iphone17.mov"
    assert result == ResultFile(name=out.name, path=str(out), remote_url=None)


def test_failed_export_keeps_the_generated_video(monkeypatch):
    result = [ResultFile(name="v.mp4", path="/x/v.mp4", remote_url="https://r/v")]
    _generated(monkeypatch, result)

    def broken(*_a, **_k):
        msg = "ffmpeg failed: boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(exports_module, "apply_preset", broken)

    assert _run_clone("/x", export="capcut") == result


def test_jobs_from_before_export_keep_their_capcut_choice(monkeypatch):
    result = [ResultFile(name="v.mp4", path=None, remote_url="https://r/v")]
    _generated(monkeypatch, result)
    seen = []
    monkeypatch.setattr(
        clone, "_exported", lambda r, export, _o, _root: seen.append(export) or r
    )

    _run_clone("/x", capcut=True)
    _run_clone("/x", capcut=False)

    assert seen == ["capcut", "original"]


def _pricing(client):
    html = client.get("/clone").text
    raw = re.search(r"data-pricing='([^']*)'", html).group(1)
    return json.loads(html_lib.unescape(raw))


def test_form_carries_each_providers_cost_rates_from_settings(client, monkeypatch):
    """clone-cost.js prices a pick from these, so a rate change in the env
    reaches the estimate without touching the JS."""
    monkeypatch.setenv("OFM_CLONE_CREDITS_PER_VIDEO_S", '{"720P": 40.0}')
    monkeypatch.setenv("OFM_KIE_USD_PER_CREDIT", "0.004")
    monkeypatch.setenv("OFM_CLONE_HF_CREDITS_PER_VIDEO_S", '{"720P": 2.0}')

    pricing = _pricing(client)

    assert set(pricing) == set(clone.CLONE_TARGETS), "every clone provider is priced"
    assert pricing["kie"]["credits_per_video_s"] == {"720P": 40.0}
    assert pricing["kie"]["usd_per_credit"] == 0.004
    assert pricing["kie"]["max_reference_s"] == 15
    assert pricing["higgsfield"]["credits_per_video_s"] == {"720P": 2.0}
    assert pricing["higgsfield"]["max_reference_s"] == 15
    assert 'id="clone-cost"' in client.get("/clone").text


def test_higgsfield_default_rate_matches_the_measured_run(client):
    """2026-10-18: a 13.09 s source at 720p cost $1.05 on Higgsfield (26.09
    billed seconds at 60% off). The per-source-second rate must reproduce it
    within a cent or two."""
    price = _pricing(client)["higgsfield"]

    estimate = 13.09 * price["credits_per_video_s"]["720P"] * price["usd_per_credit"]

    assert estimate == pytest.approx(1.05, abs=0.02)
    for res, per_s in price["credits_per_video_s"].items():
        list_usd_per_billed_s = {"480P": 0.05, "720P": 0.10, "1080P": 0.20}[res]
        assert per_s * price["usd_per_credit"] == pytest.approx(
            2 * list_usd_per_billed_s * 0.4
        )
