"""The one request lifecycle every provider shares: submit or resume, wait,
save -- driven against an in-memory provider, no HTTP."""

import pytest

from ofmhelpers.aigenproviders import runner, tasks
from ofmhelpers.aigenproviders.base import TaskState
from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderGenerationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

SPEC = get_spec("kie:wan/3-0-video")


class ScriptedProvider:
    """Answers `status` from a script of states, `submit` and `download`
    from scripts of failures to raise first (then they work)."""

    name = "kie"
    label = "Scripted"

    def __init__(self, states, submit_errors=(), download_errors=()):
        self.api_key = "key"
        self.states = list(states)
        self.submit_errors = list(submit_errors)
        self.download_errors = list(download_errors)
        self.submitted = []
        self.downloaded = []

    def submit(self, spec, payload, idempotency_key):
        self.submitted.append((spec.key, payload, idempotency_key))
        if self.submit_errors:
            raise self.submit_errors.pop(0)
        return f"task-{len(self.submitted)}"

    def status(self, task_id):
        del task_id
        state = self.states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state

    def download(self, url, dest):
        self.downloaded.append(url)
        if self.download_errors:
            raise self.download_errors.pop(0)
        dest.write_bytes(b"result")
        return dest


DONE = TaskState(state="done", urls=["https://cdn/x/out.mp4"])
PENDING = TaskState(state="pending")


@pytest.fixture(autouse=True)
def _fast(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path))
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0")
    monkeypatch.setattr(runner.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def slept(monkeypatch) -> list[float]:
    """The submit/download retry waits, recorded instead of slept."""
    monkeypatch.setenv("OFM_AIGEN_RATE_LIMIT_WAITS_S", "[10, 30, 60]")
    monkeypatch.setenv("OFM_AIGEN_RATE_LIMIT_MAX_WAIT_S", "180")
    monkeypatch.setenv("OFM_AIGEN_DOWNLOAD_RETRY_WAITS_S", "[5, 15]")
    waits: list[float] = []
    monkeypatch.setattr(runner, "_sleep", waits.append)
    return waits


def test_submits_once_waits_and_saves_the_result(tmp_path):
    provider = ScriptedProvider([PENDING, PENDING, DONE])
    seen = []

    [path] = runner.generate(provider, SPEC, {"prompt": "p"}, "job1", seen.extend)

    assert provider.submitted == [(SPEC.key, {"prompt": "p"}, "job1")]
    assert seen == ["https://cdn/x/out.mp4"]
    assert path == tmp_path / "kie_task-1.mp4"
    assert path.read_bytes() == b"result"
    assert tasks.recall("job1") is not None, "forgotten once its job is saved"


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
    provider = ScriptedProvider([ProviderAuthError("Fake", "bad key")])

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


def test_a_rate_limited_submit_waits_the_providers_hint_and_goes_again(slept):
    provider = ScriptedProvider(
        [DONE], submit_errors=[ProviderRateLimitError("slow down", retry_after_s=42)]
    )

    runner.generate(provider, SPEC, {}, "job8")

    assert len(provider.submitted) == 2
    assert {key for *_, key in provider.submitted} == {"job8"}
    assert slept == [42]
    task = tasks.recall("job8")
    assert task is not None
    assert task.task_id == "task-2"


def test_a_rate_limit_hint_past_the_budget_fails_at_once(slept):
    """Waiting longer than `rate_limit_max_wait_s` would only hold a worker:
    the run fails 'Rate limited', nothing remembered (nothing was made)."""
    provider = ScriptedProvider(
        [], submit_errors=[ProviderRateLimitError("slow down", retry_after_s=3600)]
    )

    with pytest.raises(ProviderRateLimitError):
        runner.generate(provider, SPEC, {}, "job9")

    assert len(provider.submitted) == 1
    assert slept == []
    assert tasks.recall("job9") is None


def test_the_budget_caps_all_waits_together(slept, monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_RATE_LIMIT_MAX_WAIT_S", "50")
    limited = [ProviderRateLimitError("slow", retry_after_s=20) for _ in range(4)]
    provider = ScriptedProvider([], submit_errors=limited)

    with pytest.raises(ProviderRateLimitError):
        runner.generate(provider, SPEC, {}, "job10")

    assert slept == [20, 20], "a third 20 s wait would pass 50 s"
    assert len(provider.submitted) == 3


@pytest.mark.parametrize(
    "error",
    [ProviderUnavailableError("5xx or read timeout"), ProviderAuthError("Fake", "")],
    ids=["unavailable", "auth"],
)
def test_any_other_submit_failure_is_never_resent(slept, error):
    """It may have created a paid task: one send, raised as it came."""
    provider = ScriptedProvider([], submit_errors=[error])

    with pytest.raises(type(error)):
        runner.generate(provider, SPEC, {}, "job11")

    assert len(provider.submitted) == 1
    assert slept == []


def test_a_failed_download_is_tried_again(slept):
    provider = ScriptedProvider(
        [DONE], download_errors=[ProviderUnavailableError("connection reset")]
    )

    [path] = runner.generate(provider, SPEC, {}, "job12")

    assert path.read_bytes() == b"result"
    assert provider.downloaded == ["https://cdn/x/out.mp4"] * 2
    assert slept == [5]
    assert len(provider.submitted) == 1


def test_a_download_failing_every_try_keeps_the_request(slept):
    failing = [ProviderUnavailableError("cdn down") for _ in range(3)]
    provider = ScriptedProvider([DONE], download_errors=failing)

    with pytest.raises(ProviderUnavailableError, match="cdn down"):
        runner.generate(provider, SPEC, {}, "job13")

    assert len(provider.downloaded) == 3
    assert slept == [5, 15]
    assert tasks.recall("job13") is not None, "a re-run downloads, never pays"


def test_a_download_failing_for_another_reason_is_not_retried(slept):
    """A full disk is not a provider blip: raised at once."""
    provider = ScriptedProvider([DONE], download_errors=[OSError("No space left")])

    with pytest.raises(OSError, match="No space left"):
        runner.generate(provider, SPEC, {}, "job14")

    assert len(provider.downloaded) == 1
    assert slept == []
