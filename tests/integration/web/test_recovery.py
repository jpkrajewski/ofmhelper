"""
The recovery sweeper: a job whose provider request outlived its poll is
settled later, on any provider -- re-run once the request is done (resuming
it, never paying twice), failed with the provider's reason, or left for the
next sweep. Every provider call is stubbed.
"""

import pytest
from rq.timeouts import JobTimeoutException

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.base import TaskState
from ofmhelpers.aigenproviders.errors import ProviderUnavailableError
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.aigenproviders.kaiai.provider import KieProvider
from ofmhelpers.cache import CLONE_LINKS_QUEUE, QUEUE_NAME
from ofmhelpers.web import recovery
from ofmhelpers.web.routers.registry import RESUMABLE
from ofmhelpers.web.stores.jobs import create_job, fail_job, get_job, run_job

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
    """Every provider's status call answers `answer`; `seen` records (provider,
    key, task id)."""
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
    """The re-runs recovery queued (their args), and each one's queue and
    time limit."""

    def __init__(self):
        super().__init__()
        self.queues = []
        self.timeouts = []


@pytest.fixture
def reruns(monkeypatch):
    calls = _Reruns()

    def enqueue_to(queue, *args, job_timeout=None):
        calls.append(args)
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
    """Recovery re-runs the job; the runner finds the remembered request and
    never submits a second one."""
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

    recovery.run_recovery_once()  # enqueue runs inline in tests

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
    from ofmhelpers.web.routers.generation.clone import _run_clone

    job_id = _job(task="clone")
    _remember(job_id)
    status["answer"] = TaskState(state="done", urls=["https://cdn/x.mp4"])

    recovery.run_recovery_once()

    [(_run_job, _id, fn, _kwargs)] = reruns
    assert fn is _run_clone
    assert reruns.queues == [QUEUE_NAME]


def test_a_clone_links_job_is_rerun_on_its_own_queue(status, reruns):
    """Its first run went on the clone-links pool; the re-run must too, or
    it waits behind (and holds) the default queue."""
    job_id = _job(task="clone_links")
    _remember(job_id)
    status["answer"] = TaskState(state="done", urls=["https://cdn/x.mp4"])

    assert recovery.run_recovery_once() == {job_id: "rerun"}

    [(_run_job, rerun_id, fn, _kwargs)] = reruns
    assert (rerun_id, fn) == (job_id, RESUMABLE["clone_links"])
    assert reruns.queues == [CLONE_LINKS_QUEUE]


def test_one_unreachable_provider_never_stops_the_sweep(status, reruns, monkeypatch):
    first, second = _job(), _job()
    _remember(first)
    _remember(second)
    status["answer"] = ProviderUnavailableError("down")

    assert recovery.run_recovery_once() == {}
    assert {t.job_id for t in tasks.remembered()} == {first, second}


# --- jobs cut off by a restart ----------------------------------------------


@pytest.fixture
def active(monkeypatch):
    """The job ids the queue / workers hold, as recovery reads them."""
    held: set[str] = set()
    monkeypatch.setattr(recovery, "active_job_ids", lambda: held)
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "0")
    return held


def test_a_job_cut_off_after_its_request_finished_is_resumed(status, reruns, active):
    """Prod, 2026-10-22: a deploy killed the worker during a clone's CapCut
    export; the job stayed running with nobody on it."""
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
    job_id = _job(status="running", task="download_media")

    assert recovery.run_recovery_once() == {job_id: "interrupted"}
    job = get_job(job_id)
    assert job.status == "failed"
    assert job.error == recovery.INTERRUPTED
    assert reruns == []


def test_a_job_a_worker_holds_or_the_queue_has_is_left_running(status, reruns, active):
    held = _job(status="running", task="clone", provider="higgsfield")
    _remember(held, provider="higgsfield")
    queued = _job(status="running", task="download_media")
    active.update({held, queued})

    assert recovery.run_recovery_once() == {held: "running"}
    assert get_job(queued).status == "running"
    assert status["seen"] == []


def test_a_just_created_job_is_not_cut_off(status, reruns, active, monkeypatch):
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "120")
    job_id = _job(status="running", task="download_media")
    assert recovery.run_recovery_once() == {}
    assert get_job(job_id).status == "running"


def test_without_a_view_of_the_queue_nothing_is_cut_off(status, reruns, monkeypatch):
    monkeypatch.setattr(recovery, "active_job_ids", lambda: None)
    job_id = _job(status="running", task="download_media")
    assert recovery.run_recovery_once() == {}
    assert get_job(job_id).status == "running"


def test_a_job_queued_while_a_slow_sweep_settles_is_not_cut_off(
    status, reruns, monkeypatch
):
    """Review #24: a provider status call can hold the settle loop past the
    grace; a Generate queued meanwhile is not in the view taken before it.
    Cut-off is judged against a view read after the loop."""
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "0")
    queued = _job(status="running", task="clone_links")
    views = [set()]  # the view at sweep start, from before `queued` was queued
    monkeypatch.setattr(
        recovery, "active_job_ids", lambda: views.pop(0) if views else {queued}
    )

    assert recovery.run_recovery_once() == {}

    assert get_job(queued).status == "running"
    assert views == []


def test_a_failed_job_a_worker_still_holds_is_never_rerun(status, reruns, active):
    """A job failed by mistake (or still in its tail) whose request is done:
    re-running it now would run it twice at once."""
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
    """Its tail (export, asset store) failed: re-running it every sweep
    would fail the same way, for 48 h."""
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


# --- a job of several requests (a long /clone video's pieces) ---------------


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
    """RQ ending a long run (2026-10-23: 5 pieces, then CapCut) must not
    forget the pieces already paid for: recovery finishes the job instead."""
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
