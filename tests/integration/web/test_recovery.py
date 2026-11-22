import inspect

import pytest
from rq.timeouts import JobTimeoutException
from signin import make_user

from ofmhelpers.background import recovery
from ofmhelpers.background.registry import RESTARTS, RESUMABLE
from ofmhelpers.cache import QueueName
from ofmhelpers.db.jobs.store import create_job, fail_job, get_job
from ofmhelpers.features.jobs import restart as restart_module
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.integrations.ai_providers.base import TaskState
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.integrations.ai_providers.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.integrations.ai_providers.kie.provider import KieProvider
from ofmhelpers.web.routers.generation.clone import _run_clone

MODEL = "kie:wan/3-0-video"


def _job(status="failed", task="ai", provider="kie", model=MODEL):
    job_id = create_job(task, {"provider": provider, "model": model, "prompt": "p"})
    if status == "failed":
        fail_job(job_id, "still generating, gave up polling")
    elif status == "done":
        run_job(job_id, list, {})
    return job_id


def _remember(job_id, provider="kie", model=MODEL, age_s=0.0, part=0):
    task = tasks.ProviderTask(
        job_id=job_id,
        provider=provider,
        model=model,
        task_id=f"t{part + 1}",
        api_key="key-1",
        part=part,
    )
    task.created_at -= age_s
    tasks.remember(task)
    return task


@pytest.fixture
def status(monkeypatch):
    state = {"answer": TaskState(state="pending"), "seen": []}

    def fake(self, task_id):
        state["seen"].append((self.name, self.api_key, task_id))
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return state["answer"]

    for cls in (KieProvider, HiggsfieldProvider):
        monkeypatch.setattr(cls, "status", fake)
    return state


class _Reruns(list):
    def __init__(self):
        super().__init__()
        self.queues = []
        self.timeouts = []


@pytest.fixture
def reruns(monkeypatch):
    calls = _Reruns()

    def enqueue_to(queue, fn, args, job_timeout=None):
        calls.append((fn, *args))
        calls.queues.append(queue)
        calls.timeouts.append(job_timeout)

    monkeypatch.setattr(recovery, "enqueue_to", enqueue_to)
    return calls


def test_a_finished_request_reruns_its_failed_job_with_its_key(status, reruns):
    job_id = _job()
    _remember(job_id)
    status["answer"] = TaskState(state="done", urls=["https://cdn/x.mp4"])

    assert recovery.run_recovery_once() == {job_id: "rerun"}

    assert get_job(job_id).status == "running"
    [(_run_job, rerun_id, _fn, kwargs)] = reruns
    assert rerun_id == job_id
    assert kwargs["api_key"] == "key-1", (
        "checked and re-run on the key it was made with"
    )
    assert kwargs["model"] == MODEL
    assert status["seen"] == [("kie", "key-1", "t1")]


def test_a_rerun_resumes_the_same_request(status, monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path))
    job_id = _job()
    _remember(job_id)
    status["answer"] = TaskState(state="done", urls=["https://cdn/x.mp4"])
    submitted = []
    monkeypatch.setattr(KieProvider, "submit", lambda *a, **k: submitted.append(a))
    monkeypatch.setattr(
        KieProvider,
        "download",
        lambda _self, _url, dest: dest.write_bytes(b"v") and dest,
    )

    recovery.run_recovery_once()

    assert get_job(job_id).status == "done"
    assert submitted == []
    assert tasks.recall(job_id) is None


def test_a_failed_request_fails_the_job_with_the_providers_reason(status, reruns):
    job_id = _job()
    _remember(job_id)
    status["answer"] = TaskState(state="failed", error="content flagged")

    assert recovery.run_recovery_once() == {job_id: "failed"}

    job = get_job(job_id)
    assert job.status == "failed"
    assert "kie.ai failed this generation: content flagged" in job.error
    assert tasks.recall(job_id) is None
    assert reruns == []


def test_a_still_generating_request_waits_for_the_next_sweep(status, reruns):
    job_id = _job()
    _remember(job_id)

    assert recovery.run_recovery_once() == {job_id: "pending"}
    assert tasks.recall(job_id) is not None


def test_a_running_job_is_left_alone(status, reruns):
    job_id = _job(status="running")
    _remember(job_id)

    assert recovery.run_recovery_once() == {job_id: "running"}
    assert status["seen"] == []


def test_a_done_or_deleted_job_is_forgotten(status, reruns):
    done = _job(status="done")
    _remember(done)
    _remember("gone-job")

    outcomes = recovery.run_recovery_once()

    assert outcomes == {done: "forgotten", "gone-job": "forgotten"}
    assert tasks.remembered() == []


def test_an_ancient_request_expires_instead_of_being_checked_forever(status, reruns):
    job_id = _job()
    _remember(job_id, age_s=10**7)

    assert recovery.run_recovery_once() == {job_id: "expired"}
    assert status["seen"] == []


def test_any_provider_is_recovered_the_same_way(status, reruns):
    job_id = _job(
        provider="higgsfield", model="higgsfield:alibaba/wan-3.0/text-to-video"
    )
    _remember(
        job_id, provider="higgsfield", model="higgsfield:alibaba/wan-3.0/text-to-video"
    )
    status["answer"] = TaskState(state="done", urls=["https://hf/x.mp4"])

    assert recovery.run_recovery_once() == {job_id: "rerun"}
    assert status["seen"] == [("higgsfield", "key-1", "t1")]


def test_a_clone_job_is_rerun_through_clone(status, reruns):
    job_id = _job(task="clone")
    _remember(job_id)
    status["answer"] = TaskState(state="done", urls=["https://cdn/x.mp4"])

    recovery.run_recovery_once()

    [(_run_job, _id, fn, _kwargs)] = reruns
    assert fn is _run_clone
    assert reruns.queues == [QueueName.DEFAULT]


def test_a_clone_links_job_is_rerun_on_its_own_queue(status, reruns):
    job_id = _job(task="clone_links")
    _remember(job_id)
    status["answer"] = TaskState(state="done", urls=["https://cdn/x.mp4"])

    assert recovery.run_recovery_once() == {job_id: "rerun"}

    [(_run_job, rerun_id, fn, _kwargs)] = reruns
    assert (rerun_id, fn) == (job_id, RESUMABLE["clone_links"])
    assert reruns.queues == [QueueName.CLONE_LINKS]


def test_one_unreachable_provider_never_stops_the_sweep(status, reruns, monkeypatch):
    first, second = _job(), _job()
    _remember(first)
    _remember(second)
    status["answer"] = ProviderUnavailableError("down")

    assert recovery.run_recovery_once() == {}
    assert {t.job_id for t in tasks.remembered()} == {first, second}


@pytest.fixture
def active(monkeypatch):
    held: set[str] = set()
    monkeypatch.setattr(recovery, "active_job_ids", lambda: held)
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "0")
    return held


def test_a_job_cut_off_after_its_request_finished_is_resumed(status, reruns, active):
    job_id = _job(status="running", task="clone", provider="higgsfield")
    _remember(job_id, provider="higgsfield")
    status["answer"] = TaskState(state="done", urls=["https://cdn/x.mp4"])

    assert recovery.run_recovery_once() == {job_id: "rerun"}
    [(_run_job, rerun_id, _fn, kwargs)] = reruns
    assert rerun_id == job_id
    assert kwargs["api_key"] == "key-1"


def test_a_cut_off_job_with_nothing_to_resume_fails_instead_of_spinning(
    status, reruns, active
):
    job_id = _job(status="running", task="viral_reels")

    assert recovery.run_recovery_once() == {job_id: "interrupted"}
    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == recovery.INTERRUPTED
    assert reruns == []


_LOCAL_JOBS = {
    "uniqify": (
        {
            "dir": "/u/uniqify/ab12",
            "source": "source_in.mp4",
            "stem": "out",
            "copies": 3,
            "strength": "hard",
            "mirror_pct": 0.0,
            "trim_secs": 0.1,
            "metadata": "strip",
        },
        {
            "job_dir": "/u/uniqify/ab12",
            "source": "source_in.mp4",
            "stem": "out",
            "copies": 3,
            "strength": "hard",
            "mirror_pct": 0.0,
            "trim_secs": 0.1,
            "metadata": "strip",
        },
    ),
    "repurpose": (
        {
            "dir": "/u/repurpose/cd34",
            "source": "/a/in.mp4",
            "seed": 12345,
        },
        {
            "job_dir": "/u/repurpose/cd34",
            "source": "/a/in.mp4",
            "seed": 12345,
        },
    ),
    "character_sheet": (
        {"dir": "/u/character-sheet/ef56", "files": ["00_a.png", "01_b.png"]},
        {
            "job_dir": "/u/character-sheet/ef56",
            "files": ["00_a.png", "01_b.png"],
            "assets_root": "ASSETS",
        },
    ),
    "edit_cut": (
        {"video": "/a/in.mp4", "start": 1.0, "end": 4.0, "speed": 1.0},
        {
            "video": "/a/in.mp4",
            "start": 1.0,
            "end": 4.0,
            "speed": 1.0,
            "assets_root": "ASSETS",
        },
    ),
    "threads": (
        {
            "batch": "3",
            "login_id": 3,
            "key": "reel1",
            "reel_name": "VID_1.mp4",
            "account_folder_id": "acc1",
            "seed": "ab12cd34",
            "preset": "threads",
            "folder_id": "set1",
        },
        {
            "login_id": 3,
            "key": "reel1",
            "reel_name": "VID_1.mp4",
            "account_folder_id": "acc1",
            "seed": "ab12cd34",
            "preset": "threads",
            "folder_id": "set1",
            "user_id": 7,
            "job_id": "JOB",
        },
    ),
    "edit_prepare": (
        {"video": "/a/in.mp4"},
        {"video": "/a/in.mp4", "assets_root": "ASSETS"},
    ),
    "handmade_upload": (
        {"dir": "/u/handmade/ab12", "files": ["a.mp4"]},
        {"job_dir": "/u/handmade/ab12", "files": ["a.mp4"], "job_id": "JOB"},
    ),
    "download_media": (
        {"urls": ["https://example.com/v/1"]},
        {
            "urls": ["https://example.com/v/1"],
            "assets_root": "ASSETS",
            "user_id": 7,
            "job_id": "JOB",
        },
    ),
    "download_videos": (
        {"urls": ["https://example.com/v/1"]},
        {
            "urls": ["https://example.com/v/1"],
            "assets_root": "ASSETS",
            "user_id": 7,
            "job_id": "JOB",
        },
    ),
    "download_images": (
        {"urls": ["https://example.com/p/1"]},
        {
            "urls": ["https://example.com/p/1"],
            "assets_root": "ASSETS",
            "user_id": 7,
            "job_id": "JOB",
        },
    ),
    "reels_unlock": ({}, {"user_id": 7}),
    "storage_scan": ({}, {"user_id": 7, "job_id": "JOB"}),
    "storage_bin": (
        {"part": "TRIALS", "folder_id": "F1"},
        {"user_id": 7, "job_id": "JOB", "part": "TRIALS", "folder_id": "F1"},
    ),
}


@pytest.fixture
def restarts(monkeypatch, reruns):
    monkeypatch.setattr(restart_module, "enqueue_to", recovery.enqueue_to)
    return reruns


def test_every_local_task_has_its_case_here():
    assert set(_LOCAL_JOBS) == set(RESTARTS)


def test_no_task_that_pays_a_provider_is_ever_started_over():
    paid = {*RESUMABLE, "viral_reels"}
    assert paid & set(recovery.RESTARTS) == set()


def _seven() -> str:
    made = [make_user(f"user{n}@example.com") for n in range(1, 8)]
    assert made[-1].id == 7
    return "7"


@pytest.mark.parametrize("task", sorted(_LOCAL_JOBS))
def test_a_cut_off_local_job_is_queued_again_as_its_route_queued_it(
    task, status, restarts, active
):
    params, kwargs = _LOCAL_JOBS[task]
    job_id = create_job(task, params, actor=_seven())

    assert recovery.run_recovery_once() == {job_id: "restarted"}

    [(_run_job, rerun_id, fn, queued)] = restarts
    assert (rerun_id, fn) == (job_id, RESTARTS[task].run)
    inspect.signature(fn).bind(**queued)
    root = queued.get("assets_root")
    stand_ins = (("ASSETS", root), ("JOB", job_id))
    assert queued == {
        name: next((real for mark, real in stand_ins if value == mark), value)
        for name, value in kwargs.items()
    }
    assert root is None or root.endswith("/users/7")
    job = get_job(job_id)
    assert (job.status, job.error, job.params["restarts"]) == ("running", None, 1)


def test_outside_production_a_cut_off_local_job_is_left_as_copied(
    status, restarts, active, monkeypatch
):
    monkeypatch.delenv("OFM_RECOVERY_RESTARTS")
    params, _kwargs = _LOCAL_JOBS["download_media"]
    job_id = create_job("download_media", params, actor=_seven())

    assert recovery.run_recovery_once() == {job_id: "left"}

    assert restarts == []
    job = get_job(job_id)
    assert (job.status, job.error) == ("running", None)
    monkeypatch.setenv("OFM_ENV", "production")
    assert recovery.run_recovery_once() == {job_id: "restarted"}


def test_a_restarted_local_job_keeps_its_queue_and_time_limit(
    status, restarts, active, monkeypatch
):
    monkeypatch.setenv("OFM_UNIQIFY_ENCODE_TIMEOUT_S", "600")
    monkeypatch.setenv("OFM_EDIT_CUT_TIMEOUT_S", "300")
    monkeypatch.setenv("OFM_REPURPOSE_JOB_TIMEOUT_S", "2400")
    create_job("repurpose", _LOCAL_JOBS["repurpose"][0], actor=_seven())
    create_job("edit_cut", _LOCAL_JOBS["edit_cut"][0], actor=_seven())
    create_job("uniqify", _LOCAL_JOBS["uniqify"][0], actor=_seven())

    recovery.run_recovery_once()

    by_function = dict(
        zip(
            [args[2].__name__ for args in restarts],
            zip(restarts.queues, restarts.timeouts, strict=True),
            strict=True,
        )
    )
    assert by_function == {
        "run_repurpose": (QueueName.MEDIA, 2400),
        "run_cut": (QueueName.EDIT, 360),
        "run_uniqify": (QueueName.DEFAULT, None),
    }


def test_a_local_job_cut_off_again_and_again_fails(status, restarts, active, caplog):
    params = {**_LOCAL_JOBS["uniqify"][0], "restarts": restart_module.RESTARTS_MAX}
    job_id = create_job("uniqify", params, actor=_seven())

    assert recovery.run_recovery_once() == {job_id: "interrupted"}

    assert restarts == []
    assert get_job(job_id).error == recovery.INTERRUPTED
    (lost,) = [r for r in caplog.records if r.name == recovery.__name__]
    assert (lost.levelname, lost.getMessage()) == (
        "ERROR",
        f"recovery: job {job_id} (uniqify) was cut off by a restart",
    )


def test_a_second_restart_runs_with_the_same_arguments(status, restarts, active):
    params = {**_LOCAL_JOBS["uniqify"][0], "restarts": 1}
    job_id = create_job("uniqify", params, actor=_seven())

    assert recovery.run_recovery_once() == {job_id: "restarted"}

    [(_run_job, _id, _fn, queued)] = restarts
    assert queued == _LOCAL_JOBS["uniqify"][1]
    assert get_job(job_id).params["restarts"] == 2


def test_a_local_job_whose_params_cannot_be_read_fails_as_interrupted(
    status, restarts, active
):
    job_id = create_job("download_media", {"links": "an old shape"}, actor=_seven())

    assert recovery.run_recovery_once() == {job_id: "interrupted"}

    assert restarts == []
    assert "restarts" not in get_job(job_id).params


def test_a_job_a_worker_holds_or_the_queue_has_is_left_running(status, reruns, active):
    held = _job(status="running", task="clone", provider="higgsfield")
    _remember(held, provider="higgsfield")
    queued = _job(status="running", task="viral_reels")
    active.update({held, queued})

    assert recovery.run_recovery_once() == {held: "running"}
    assert get_job(queued).status == "running"
    assert status["seen"] == []


def test_a_just_created_job_is_not_cut_off(status, reruns, active, monkeypatch):
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "120")
    job_id = _job(status="running", task="viral_reels")
    assert recovery.run_recovery_once() == {}
    assert get_job(job_id).status == "running"


def test_without_a_view_of_the_queue_nothing_is_cut_off(status, reruns, monkeypatch):
    monkeypatch.setattr(recovery, "active_job_ids", lambda: None)
    job_id = _job(status="running", task="viral_reels")
    assert recovery.run_recovery_once() == {}
    assert get_job(job_id).status == "running"


def test_a_job_queued_while_a_slow_sweep_settles_is_not_cut_off(
    status, reruns, monkeypatch
):
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "0")
    queued = _job(status="running", task="clone_links")
    views = [set()]
    monkeypatch.setattr(
        recovery, "active_job_ids", lambda: views.pop(0) if views else {queued}
    )

    assert recovery.run_recovery_once() == {}

    assert get_job(queued).status == "running"
    assert views == []


def test_a_failed_job_a_worker_still_holds_is_never_rerun(status, reruns, active):
    job_id = _job(task="clone_links")
    _remember(job_id)
    status["answer"] = TaskState(state="done", urls=["https://cdn/x.mp4"])
    active.add(job_id)

    assert recovery.run_recovery_once() == {job_id: "running"}

    assert reruns == []
    assert status["seen"] == []
    assert get_job(job_id).status == "failed"


def test_a_saved_job_forgets_its_request():
    job_id = _job(status="running")
    _remember(job_id)
    run_job(job_id, list, {})
    assert tasks.recall(job_id) is None


def _fails_after_the_result(**_kwargs):
    msg = "CapCut export failed"
    raise RuntimeError(msg)


def _provider_still_generating(**_kwargs):
    msg = "Kie is still generating"
    raise ProviderUnavailableError(msg)


def test_a_job_failing_after_its_result_forgets_its_request(status, reruns):
    job_id = _job(status="running")
    _remember(job_id)
    run_job(job_id, _fails_after_the_result, {})
    assert get_job(job_id).status == "failed"
    assert tasks.recall(job_id) is None
    assert recovery.run_recovery_once() == {}
    assert reruns == []


def test_a_job_failing_at_the_provider_keeps_its_request_for_recovery():
    job_id = _job(status="running")
    _remember(job_id)
    run_job(job_id, _provider_still_generating, {})
    assert get_job(job_id).status == "failed"
    assert tasks.recall(job_id) is not None


def test_every_piece_is_remembered_on_its_own_and_forgotten_together():
    job_id = _job(status="running")
    for part in range(3):
        _remember(job_id, part=part)

    assert {t.part for t in tasks.remembered() if t.job_id == job_id} == {0, 1, 2}
    assert tasks.recall(job_id, 1).task_id == "t2"
    assert tasks.recall(job_id).task_id == "t1"

    tasks.forget(job_id)

    assert [t for t in tasks.remembered() if t.job_id == job_id] == []


def test_a_job_whose_pieces_are_all_done_is_rerun_once(status, reruns):
    job_id = _job(task="clone", model="kie:wan/3-0-video")
    for part in range(3):
        _remember(job_id, part=part)
    status["answer"] = TaskState(state="done", urls=["https://x/v.mp4"])

    outcomes = recovery.run_recovery_once()

    assert outcomes == {job_id: "rerun"}
    assert len(reruns) == 1
    assert {seen[2] for seen in status["seen"]} == {"t1", "t2", "t3"}


def test_a_job_with_a_piece_still_generating_waits(status, reruns, monkeypatch):
    job_id = _job(task="clone", model="kie:wan/3-0-video")
    for part in range(3):
        _remember(job_id, part=part)
    done = TaskState(state="done", urls=["https://x/v.mp4"])
    answers = {"t1": done, "t2": TaskState(state="pending"), "t3": done}
    monkeypatch.setattr(KieProvider, "status", lambda _self, task_id: answers[task_id])

    assert recovery.run_recovery_once() == {job_id: "pending"}
    assert reruns == []
    assert len([t for t in tasks.remembered() if t.job_id == job_id]) == 3


def test_a_job_with_a_refused_piece_fails_and_forgets_every_piece(
    status, reruns, monkeypatch
):
    job_id = _job(task="clone", model="kie:wan/3-0-video")
    for part in range(3):
        _remember(job_id, part=part)
    done = TaskState(state="done", urls=["https://x/v.mp4"])
    answers = {
        "t1": done,
        "t2": TaskState(state="failed", error="flagged as sensitive"),
        "t3": done,
    }
    monkeypatch.setattr(KieProvider, "status", lambda _self, task_id: answers[task_id])

    assert recovery.run_recovery_once() == {job_id: "failed"}
    assert reruns == []
    assert "flagged as sensitive" in get_job(job_id).error
    assert [t for t in tasks.remembered() if t.job_id == job_id] == []


def test_a_job_rq_ends_at_its_time_limit_keeps_every_piece_for_recovery():
    job_id = _job(status="running", task="clone", model="kie:wan/3-0-video")
    for part in range(3):
        _remember(job_id, part=part)

    def killed(**_kwargs):
        msg = "Task exceeded maximum timeout value (2700 seconds)"
        raise JobTimeoutException(msg)

    run_job(job_id, killed, {})

    assert get_job(job_id).status == "failed"
    assert len([t for t in tasks.remembered() if t.job_id == job_id]) == 3


def test_a_one_piece_job_is_rerun_with_the_queues_limit(status, reruns):
    job_id = _job()
    _remember(job_id)
    status["answer"] = TaskState(state="done", urls=["https://x/v.mp4"])

    recovery.run_recovery_once()

    assert reruns.timeouts == [None]


def test_a_job_with_a_piece_whose_answer_was_lost_is_not_run_again_yet(status, reruns):
    job_id = _job()
    _remember(job_id, part=0)
    tasks.remember_sent(
        tasks.ProviderTask(
            job_id=job_id,
            part=1,
            provider="kie",
            model=MODEL,
            task_id="",
            api_key="key-1",
        )
    )
    status["answer"] = TaskState(state="done", urls=["https://cdn/x.mp4"])

    assert recovery.run_recovery_once() == {job_id: "waiting"}
    assert reruns == []

    tasks.found(job_id, 1, "t2")

    assert recovery.run_recovery_once() == {job_id: "rerun"}
    assert len(reruns) == 1
