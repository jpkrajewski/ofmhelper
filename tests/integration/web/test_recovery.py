"""
The recovery sweeper: a job whose provider request outlived its poll is
settled later, on any provider -- re-run once the request is done (resuming
it, never paying twice), failed with the provider's reason, or left for the
next sweep. Every provider call is stubbed.
"""

import pytest

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.base import TaskState
from ofmhelpers.aigenproviders.errors import ProviderUnavailableError
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.aigenproviders.kaiai.provider import KieProvider
from ofmhelpers.web import recovery
from ofmhelpers.web.stores.jobs import create_job, fail_job, get_job, run_job

MODEL = "kie:wan/3-0-video"


def _job(status="failed", task="ai", provider="kie", model=MODEL):
    job_id = create_job(task, {"provider": provider, "model": model, "prompt": "p"})
    if status == "failed":
        fail_job(job_id, "still generating, gave up polling")
    elif status == "done":
        run_job(job_id, list, {})
    return job_id


def _remember(job_id, provider="kie", model=MODEL, age_s=0.0):
    task = tasks.ProviderTask(
        job_id=job_id, provider=provider, model=model, task_id="t1", api_key="key-1"
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


@pytest.fixture
def reruns(monkeypatch):
    calls = []
    monkeypatch.setattr(recovery, "enqueue", lambda *args: calls.append(args))
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


def test_one_unreachable_provider_never_stops_the_sweep(status, reruns, monkeypatch):
    first, second = _job(), _job()
    _remember(first)
    _remember(second)
    status["answer"] = ProviderUnavailableError("down")

    assert recovery.run_recovery_once() == {}
    assert {t.job_id for t in tasks.remembered()} == {first, second}
