# Copyright (C) 2026 jpkrajewski

import json

import pytest
from fastapi.testclient import TestClient
from idempotent import keyed
from signin import make_user, sign_in
from spoof_draws import drawn_step

from ofmhelpers.cache import QueueName
from ofmhelpers.db.devices import store as devices
from ofmhelpers.db.devices.models import DeviceFields, LoginFields
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.schema import GenerationRecord, JobCost, JobMeta
from ofmhelpers.db.jobs.store import (
    create_job,
    get_job,
    keep_cost,
    list_jobs,
    set_job_creator,
)
from ofmhelpers.db.tracked_accounts.store import creators_of, track
from ofmhelpers.features.assets import store as task_helper_uploads
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.repurpose import service as repurpose
from ofmhelpers.features.repurpose.executions import JobExecutions
from ofmhelpers.media.repurpose.pipelines.errors import StillSoundsLikeSourceError
from ofmhelpers.media.repurpose.pipelines.models import (
    Execution,
    ExecutionKind,
    RepurposeResult,
    Stage,
    Verdict,
)
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
        repurpose.TASK,
    }


def test_blank_copies_make_four(client, queued):
    r = _run(client, copies="")

    assert r.status_code == 202, r.text
    assert len(queued) == 4


def test_the_form_starts_with_four_copies(client):
    html = client.get("/spoof").text

    assert 'name="copies" min="1"' in html
    assert 'value="4"' in html


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
        "/spoof/run",
        data=form,
        files=[("video", ("a.mp4", b"aaaa"))],
        headers=key,
    )
    other = client.post(
        "/spoof/run",
        data=form,
        files=[("video", ("a.mp4", b"bbbb"))],
        headers=key,
    )

    assert (first.status_code, other.status_code) == (202, 400)
    assert len(queued) == 1


def test_a_non_video_is_refused_at_the_boundary(client, queued):
    r = client.post(
        "/spoof/run",
        data={"video_manifest": NEW, "copies": "1"},
        files=[("video", ("notes.txt", b"not a video"))],
        headers=keyed(),
    )

    assert r.status_code == 422
    assert queued == []


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
        "repurpose",
        {"source": "s.mp4", "seed": 1},
        actor=str(client.user.id),
    )
    run_job(job, lambda: [{"name": copy.name, "path": str(copy)}], {})
    phone = devices.add_device(
        DeviceFields(
            label="Phone 01",
            model_id="iphone-13",
            color_id="blue",
            owner="Anna",
        ),
    )
    login = devices.add_login(
        phone,
        LoginFields(service="instagram", username="ann.ig"),
    )
    assert login is not None
    upload = upload_store.queue(job, 0, login.id, client.user.id, "ann.ig - Anna")
    assert upload is not None
    upload_store.finish(upload, drive_file_id="f1", drive_url="https://drive/x")

    html = client.get("/spoof").text

    assert "In Drive" in html
    assert "ann.ig - Anna" in html


def test_a_failed_copy_keeps_every_execution_on_its_job_row(client):
    job = create_job(
        "repurpose",
        {"source": "s.mp4", "seed": 1},
        actor=str(client.user.id),
    )
    picture_run = Execution(
        number=1,
        kind=ExecutionKind.PICTURE,
        preset="spoof",
        strength=1.0,
        seconds=1.5,
        verdict=Verdict(),
    )
    audio_run = Execution(
        number=2,
        kind=ExecutionKind.AUDIO,
        preset="spoof",
        strength=1.0,
        seconds=1.5,
        verdict=Verdict(broken_audio="IMG_1.mov: no audio stream"),
    )
    executions = JobExecutions(job)

    def fails() -> None:
        executions.keep(picture_run)
        executions.keep(audio_run)
        msg = "Sound came out broken after 2 tries: IMG_1.mov: no audio stream"
        raise StillSoundsLikeSourceError(msg)

    run_job(job, fails, {})

    stored = get_job(job)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == (
        "Sound came out broken after 2 tries: IMG_1.mov: no audio stream"
    )
    assert stored.meta.executions == [
        picture_run.model_dump(mode="json"),
        audio_run.model_dump(mode="json"),
    ]
    assert Execution.model_validate(stored.meta.executions[1]) == audio_run


def test_keeping_a_jobs_cost_leaves_its_executions_in_place(client):
    job = create_job(
        "repurpose",
        {"source": "s.mp4", "seed": 2},
        actor=str(client.user.id),
    )
    picture_run = Execution(
        number=1,
        kind=ExecutionKind.PICTURE,
        preset="spoof",
        strength=1.0,
        seconds=1.5,
        verdict=Verdict(),
    )
    JobExecutions(job).keep(picture_run)
    generation = GenerationRecord(provider="kie", model="veo", task_id="t1")

    keep_cost(job, JobMeta(generations=[generation]), JobCost())

    stored = get_job(job)
    assert stored is not None
    assert stored.meta.executions == [picture_run.model_dump(mode="json")]
    assert stored.meta.generations == [generation]


def test_a_queued_copy_is_told_its_job_so_it_can_keep_its_executions(client, queued):
    r = _run(client, copies="2")

    assert [kwargs["job_id"] for _, kwargs in queued] == r.json()["job_ids"]


def _row(label: str, value: str) -> str:
    return (
        f'<div class="menu-text"><span class="menu-text-label">{label}</span>'
        f"<span>{value}</span></div>"
    )


def test_a_copy_card_names_the_preset_its_strength_and_the_tries_it_took(
    client,
    tmp_path,
):
    copy = tmp_path / "IMG_4321.mov"
    copy.write_bytes(b"a copy")
    job = create_job(
        "repurpose",
        {"source": "s.mp4", "seed": 4},
        actor=str(client.user.id),
    )
    picture_run = Execution(
        number=1,
        kind=ExecutionKind.PICTURE,
        preset="spoof_fallback",
        strength=1.02,
        seconds=30.0,
        verdict=Verdict(),
    )
    audio_run = Execution(
        number=2,
        kind=ExecutionKind.AUDIO,
        preset="spoof_fallback",
        strength=1.02,
        seconds=1.3,
        verdict=Verdict(),
    )
    result = RepurposeResult(
        name=copy.name,
        path=str(copy),
        stage=Stage(preset="spoof_fallback"),
        attempts=5,
        strength=1.02,
        executions=[picture_run, picture_run, picture_run, audio_run, audio_run],
    )
    run_job(job, lambda: [result], {})

    html = client.get("/spoof").text

    assert "See spoof settings" in html
    assert _row("Preset", "spoof_fallback") in html
    assert _row("Strength", "1.02") in html
    assert _row("Picture try", "3") in html
    assert _row("Audio retries", "2") in html
    assert _row("Sound", "unique") in html


def test_a_copy_that_kept_its_first_sound_says_so_in_its_settings(client, tmp_path):
    copy = tmp_path / "IMG_4323.mov"
    copy.write_bytes(b"a copy")
    job = create_job(
        "repurpose",
        {"source": "s.mp4", "seed": 6, "mirror": "mirrored"},
        actor=str(client.user.id),
    )
    result = RepurposeResult(
        name=copy.name,
        path=str(copy),
        step=drawn_step(6),
        stage=Stage(preset="spoof"),
        attempts=4,
        kept_first_sound=True,
    )
    run_job(job, lambda: [result], {})

    html = client.get("/spoof").text

    assert _row("Sound", "still matches the source: first sound kept") in html
    assert "mirrored (chosen)</span>" in html
    assert "(random)" not in html


def test_the_mirror_choice_is_stored_with_the_job_and_given_to_the_worker(
    client,
    queued,
):
    r = _run(client, copies="1", mirror="mirrored")

    assert r.status_code == 202, r.text
    [(_, kwargs)] = queued
    [job_id] = r.json()["job_ids"]
    stored = get_job(job_id)
    assert stored is not None
    assert kwargs["mirror"] == stored.params["mirror"] == "mirrored"


def test_a_copy_is_mirrored_at_random_unless_the_form_says_otherwise(client, queued):
    r = _run(client, copies="1")

    assert r.status_code == 202, r.text
    [(_, kwargs)] = queued
    assert kwargs["mirror"] == "random"


def test_a_mirror_choice_the_form_does_not_offer_is_refused(client, queued):
    r = _run(client, copies="1", mirror="sideways")

    assert r.status_code == 422
    assert queued == []


def test_the_form_offers_the_three_mirror_choices_with_random_picked(client):
    html = client.get("/spoof").text

    assert '<option value="random" selected>Random</option>' in html
    assert '<option value="mirrored">Mirrored</option>' in html
    assert '<option value="not_mirrored">Not mirrored</option>' in html


def test_a_copy_from_before_executions_counts_every_attempt_as_a_picture_try(
    client,
    tmp_path,
):
    copy = tmp_path / "IMG_4322.mov"
    copy.write_bytes(b"a copy")
    job = create_job(
        "repurpose",
        {"source": "s.mp4", "seed": 5},
        actor=str(client.user.id),
    )
    stored = {
        "name": copy.name,
        "path": str(copy),
        "stage": {"preset": "spoof"},
        "attempts": 13,
        "strength": 1.04,
    }
    run_job(job, lambda: [stored], {})

    html = client.get("/spoof").text

    assert _row("Preset", "spoof") in html
    assert _row("Strength", "1.04") in html
    assert _row("Picture try", "13") in html
    assert _row("Audio retries", "0") in html


def _copy_drawn(client, tmp_path, seed: int) -> None:
    copy = tmp_path / f"IMG_{seed}.mov"
    copy.write_bytes(b"a copy")
    job = create_job(
        "repurpose",
        {"source": "s.mp4", "seed": seed},
        actor=str(client.user.id),
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
    assert html.count(_row("Mirror", "mirrored (random)")) == mirrored
    assert html.count(_row("Mirror", "not mirrored (random)")) == (
        len(drawn) - mirrored
    )
    turn = drawn[0].rotate or 0.0
    side = "right" if turn > 0 else "left"
    assert _row("Turn", f"{abs(turn):.1f}° {side}") in html


def _creator(owner_id: int, name: str) -> int:
    track("instagram", f"{name.lower()}.{owner_id}", name, user_id=owner_id)
    for creator in creators_of(owner_id):
        if creator.name == name:
            return creator.id
    raise AssertionError(name)


def _done_copy(client, tmp_path, seed: int, creator_id: int | None) -> str:
    copy = tmp_path / f"IMG_{seed}.mov"
    copy.write_bytes(b"a copy")
    job = create_job(
        "repurpose",
        {"source": "s.mp4", "seed": seed},
        actor=str(client.user.id),
    )
    if creator_id is not None:
        set_job_creator(job, creator_id)
    run_job(job, lambda: [{"name": copy.name, "path": str(copy)}], {})
    return job


def test_the_form_lists_the_owners_models_after_no_model(client):
    mia = _creator(client.user.id, "Mia")
    yuki = _creator(client.user.id, "Yuki")
    _creator(make_user("other@example.com").id, "Zoe")

    html = client.get("/spoof").text

    assert '<label for="rp-creator">Model</label>' in html
    assert '<option value="" selected>No model</option>' in html
    assert f'<option value="{mia}">Mia</option>' in html
    assert f'<option value="{yuki}">Yuki</option>' in html
    assert "Zoe" not in html
    assert "All models" in html
    assert f'href="/spoof?creator={mia}"' in html


def test_without_models_the_page_has_no_model_field_and_no_models_menu(client):
    _creator(make_user("other@example.com").id, "Zoe")

    html = client.get("/spoof").text

    assert "rp-creator" not in html
    assert "spoof-models" not in html
    assert "All models" not in html


def test_a_run_with_a_model_keeps_it_on_every_job_row(client, queued):
    mia = _creator(client.user.id, "Mia")

    r = _run(client, copies="2", creator=str(mia))

    assert r.status_code == 202, r.text
    stored = [get_job(job_id) for job_id in r.json()["job_ids"]]
    assert [job.creator_id for job in stored if job is not None] == [mia, mia]
    assert all("creator" not in kwargs for _, kwargs in queued)


def test_a_run_with_no_model_keeps_none(client, queued):
    _creator(client.user.id, "Mia")

    r = _run(client, copies="1", creator="")

    assert r.status_code == 202, r.text
    [job_id] = r.json()["job_ids"]
    stored = get_job(job_id)
    assert stored is not None
    assert stored.creator_id is None


def test_another_owners_model_is_refused_and_queues_nothing(client, queued):
    zoe = _creator(make_user("other@example.com").id, "Zoe")
    before = len(list_jobs())

    r = _run(client, copies="1", creator=str(zoe))

    assert r.status_code == 400
    assert r.json() == {"detail": "Pick one of your models"}
    assert queued == []
    assert len(list_jobs()) == before


@pytest.mark.parametrize("junk", ["0", "-3", "mia"])
def test_a_model_id_that_is_not_a_positive_number_is_refused(client, queued, junk):
    r = _run(client, copies="1", creator=junk)

    assert r.status_code == 422
    assert queued == []


def test_the_models_filter_shows_only_that_models_copies(client, tmp_path):
    mia = _creator(client.user.id, "Mia")
    yuki = _creator(client.user.id, "Yuki")
    of_mia = _done_copy(client, tmp_path, 1, mia)
    of_yuki = _done_copy(client, tmp_path, 2, yuki)
    of_nobody = _done_copy(client, tmp_path, 3, None)

    everything = client.get("/spoof").text
    only_mia = client.get(f"/spoof?creator={mia}").text

    for job_id in (of_mia, of_yuki, of_nobody):
        assert f'data-job-id="{job_id}"' in everything
    assert f'data-job-id="{of_mia}"' in only_mia
    assert f'data-job-id="{of_yuki}"' not in only_mia
    assert f'data-job-id="{of_nobody}"' not in only_mia
    assert f'href="/spoof?creator={mia}" aria-current="true"' in only_mia


def test_another_owners_model_in_the_filter_shows_only_the_readers_own_copies(
    client,
    tmp_path,
):
    other = make_user("other@example.com")
    zoe = _creator(other.id, "Zoe")
    theirs = create_job(
        "repurpose",
        {"source": "s.mp4", "seed": 9},
        actor=str(other.id),
    )
    set_job_creator(theirs, zoe)
    mine = _done_copy(client, tmp_path, 4, None)

    html = client.get(f"/spoof?creator={zoe}").text

    assert f'data-job-id="{theirs}"' not in html
    assert f'data-job-id="{mine}"' in html
    assert "Zoe" not in html


def test_a_copy_card_names_its_model_in_the_spoof_settings(client, tmp_path):
    mia = _creator(client.user.id, "Mia")
    with_model = _done_copy(client, tmp_path, 5, mia)
    _done_copy(client, tmp_path, 6, None)

    html = client.get("/spoof").text
    card = client.get(f"/spoof/cards/{with_model}").text

    assert html.count(_row("Model", "Mia")) == 1
    assert "See spoof settings" in card
    assert _row("Model", "Mia") in card
    assert f'<span data-creator-id="{mia}" hidden></span>' in card
    assert html.count("data-creator-id=") == 1
