import json

import pytest
from fastapi.testclient import TestClient
from idempotent import keyed
from signin import sign_in
from spoof_draws import drawn_step

from ofmhelpers.cache import QueueName
from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.models import DeviceFields, LoginFields
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.store import create_job, list_jobs
from ofmhelpers.features.assets import store as task_helper_uploads
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.repurpose import service as repurpose
from ofmhelpers.web.main import app

CLIP = ("video", ("clip.mp4", b"not really a video"))
NEW = json.dumps([{"kind": "new"}])


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(task_helper_uploads, "assets_root", lambda: tmp_path / "assets")
    monkeypatch.setattr(repurpose, "upload_root", lambda: tmp_path / "repurpose")


@pytest.fixture
def queued(monkeypatch):
    calls: list[tuple[QueueName, dict]] = []

    def enqueue_to(queue, _run_job, args, **_options):
        calls.append((queue, args[2]))

    monkeypatch.setattr(repurpose, "enqueue_to", enqueue_to)
    return calls


@pytest.fixture
def client():
    c = TestClient(app)
    c.user = sign_in(c)  # type: ignore[attr-defined]
    return c


def _run(client, **data):
    return client.post(
        "/spoof/run",
        data={"video_manifest": NEW, **data},
        files=[CLIP],
        headers=keyed(),
    )


def test_each_copy_is_its_own_job_on_the_media_queue_with_its_own_seed(client, queued):
    r = _run(client, copies="3")

    assert r.status_code == 202, r.text
    assert len(r.json()["job_ids"]) == 3
    assert [queue for queue, _ in queued] == [QueueName.MEDIA] * 3
    assert len({kwargs["seed"] for _, kwargs in queued}) == 3
    assert len({kwargs["job_dir"] for _, kwargs in queued}) == 3
    assert {j.task for j in list_jobs() if j.id in r.json()["job_ids"]} == {
        repurpose.TASK
    }


def test_blank_copies_make_one(client, queued):
    r = _run(client, copies="")

    assert r.status_code == 202, r.text
    assert len(queued) == 1


def test_more_copies_than_the_limit_are_refused(client, queued):
    r = _run(client, copies="99")

    assert r.status_code == 422
    assert queued == []


def test_a_second_click_with_the_same_key_queues_nothing_more(client, queued):
    key = keyed()
    form = {"video_manifest": NEW, "copies": "2"}

    first = client.post("/spoof/run", data=form, files=[CLIP], headers=key)
    again = client.post("/spoof/run", data=form, files=[CLIP], headers=key)

    assert (first.status_code, again.status_code) == (202, 200)
    assert again.json() == first.json()
    assert len(queued) == 2


def test_the_same_key_with_another_video_of_the_same_size_is_refused(client, queued):
    key = keyed()
    form = {"video_manifest": NEW, "copies": "1"}

    first = client.post(
        "/spoof/run", data=form, files=[("video", ("a.mp4", b"aaaa"))], headers=key
    )
    other = client.post(
        "/spoof/run", data=form, files=[("video", ("a.mp4", b"bbbb"))], headers=key
    )

    assert (first.status_code, other.status_code) == (202, 400)
    assert len(queued) == 1


def test_no_video_is_refused(client, queued):
    r = client.post("/spoof/run", data={"copies": "1"}, headers=keyed())

    assert r.status_code == 400
    assert r.json() == {"detail": "Pick one video"}
    assert queued == []


def test_the_page_names_the_phone_and_offers_no_knobs(client):
    html = client.get("/spoof").text

    assert "iPhone 16 Pro Max" in html
    assert "rp-knobs" not in html


def test_a_copy_uploaded_to_drive_says_so_on_its_card(client, tmp_path):
    copy = tmp_path / "IMG_1234.mov"
    copy.write_bytes(b"a copy")
    job = create_job(
        "repurpose", {"source": "s.mp4", "seed": 1}, actor=str(client.user.id)
    )
    run_job(job, lambda: [{"name": copy.name, "path": str(copy)}], {})
    phone = devices.add_device(
        DeviceFields(
            label="Phone 01", model_id="iphone-13", color_id="blue", owner="Anna"
        )
    )
    login = devices.add_login(
        phone, LoginFields(service="instagram", username="ann.ig")
    )
    assert login is not None
    upload = upload_store.queue(job, 0, login.id, client.user.id, "ann.ig - Anna")
    assert upload is not None
    upload_store.finish(upload, drive_file_id="f1", drive_url="https://drive/x")

    html = client.get("/spoof").text

    assert "In Drive" in html
    assert "ann.ig - Anna" in html


def _copy_drawn(client, tmp_path, seed: int) -> None:
    copy = tmp_path / f"IMG_{seed}.mov"
    copy.write_bytes(b"a copy")
    job = create_job(
        "repurpose", {"source": "s.mp4", "seed": seed}, actor=str(client.user.id)
    )
    step = drawn_step(seed).model_dump(mode="json")
    run_job(job, lambda: [{"name": copy.name, "path": str(copy), "step": step}], {})


def test_a_copy_card_says_if_it_was_mirrored_and_how_far_it_turned(client, tmp_path):
    seeds = range(8)
    for seed in seeds:
        _copy_drawn(client, tmp_path, seed)
    drawn = [drawn_step(seed).video for seed in seeds]

    html = client.get("/spoof").text

    mirrored = sum(d.mirror for d in drawn)
    assert 0 < mirrored < len(drawn)
    assert html.count("· mirrored") == mirrored
    assert html.count("· not&nbsp;mirrored") == len(drawn) - mirrored
    turn = drawn[0].rotate or 0.0
    side = "right" if turn > 0 else "left"
    assert f"· turned&nbsp;{abs(turn):.1f}°&nbsp;{side}" in html
