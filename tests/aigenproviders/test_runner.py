"""The one request lifecycle every provider shares: submit or resume, wait,
save -- driven against an in-memory provider, no HTTP."""

import pytest

from ofmhelpers.aigenproviders import runner, tasks
from ofmhelpers.aigenproviders.base import TaskState
from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderGenerationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

SPEC = get_spec("kie:wan/3-0-video")


class ScriptedProvider:
    """Answers `status` from a script of states (an exception is raised)."""

    name = "kie"
    label = "Scripted"

    def __init__(self, states):
        self.api_key = "key"
        self.states = list(states)
        self.submitted = []
        self.downloaded = []

    def submit(self, spec, payload, idempotency_key):
        self.submitted.append((spec.key, payload, idempotency_key))
        return f"task-{len(self.submitted)}"

    def status(self, task_id):
        del task_id
        state = self.states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state

    def download(self, url, dest):
        dest.write_bytes(b"result")
        self.downloaded.append(url)
        return dest


DONE = TaskState(state="done", urls=["https://cdn/x/out.mp4"])
PENDING = TaskState(state="pending")


@pytest.fixture(autouse=True)
def _fast(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path))
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0")
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)


def test_submits_once_waits_and_saves_the_result(tmp_path):
    provider = ScriptedProvider([PENDING, PENDING, DONE])
    seen = []

    [path] = runner.generate(provider, SPEC, {"prompt": "p"}, "job1", seen.extend)

    assert provider.submitted == [(SPEC.key, {"prompt": "p"}, "job1")]
    assert seen == ["https://cdn/x/out.mp4"]
    assert path == tmp_path / "kie_task-1.mp4"
    assert path.read_bytes() == b"result"
    assert tasks.recall("job1") is None, "a saved result needs no resuming"


def test_a_rerun_resumes_the_remembered_request_instead_of_paying_again():
    tasks.remember(
        tasks.ProviderTask(
            job_id="job2", provider="kie", model=SPEC.key, task_id="old", api_key="k"
        )
    )
    provider = ScriptedProvider([DONE])

    runner.generate(provider, SPEC, {"prompt": "p"}, "job2")

    assert provider.submitted == []


def test_a_transient_status_error_is_retried():
    provider = ScriptedProvider([ProviderUnavailableError("blip"), DONE])

    assert runner.generate(provider, SPEC, {}, "job3")


def test_a_fatal_status_error_is_raised():
    provider = ScriptedProvider([ProviderAuthError("bad key")])

    with pytest.raises(ProviderAuthError):
        runner.generate(provider, SPEC, {}, "job4")


def test_a_failed_generation_fails_with_the_providers_reason_and_is_forgotten():
    provider = ScriptedProvider([TaskState(state="failed", error="nsfw input")])

    with pytest.raises(ProviderGenerationError, match="nsfw input"):
        runner.generate(provider, SPEC, {}, "job5")
    assert tasks.recall("job5") is None


def test_a_timeout_keeps_the_request_for_recovery(monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_VIDEO_POLL_TIMEOUT_S", "0")
    provider = ScriptedProvider([])

    with pytest.raises(ProviderTimeoutError, match="recovery finishes"):
        runner.generate(provider, SPEC, {}, "job6")
    task = tasks.recall("job6")
    assert task is not None
    assert (task.provider, task.task_id, task.api_key) == ("kie", "task-1", "key")


def test_several_results_are_saved_side_by_side(tmp_path):
    urls = ["https://cdn/a.png", "https://cdn/b.png?sig=1"]
    provider = ScriptedProvider([TaskState(state="done", urls=urls)])

    paths = runner.generate(provider, get_spec("kie:nano-banana-pro"), {}, "job7")

    assert [p.name for p in paths] == ["kie_task-1_0.png", "kie_task-1_1.png"]
