"""
Covers the "bulletproof download" recovery path: when a kie.ai generation
outlives the in-request poll (TimeoutError), the task is still in
tasks.jsonl and the background sweeper must eventually download it --
without ever re-checking tasks that are already handled, failed, or too old
to recover. All network calls are stubbed.
"""

import json
import time

import pytest

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.web import recovery


@pytest.fixture
def client(tmp_path):
    return KieAIClient(
        api_key="test-key",
        out_dir=tmp_path / "out",
        task_log=tmp_path / "tasks.jsonl",
        completions_log=tmp_path / "completions.jsonl",
        resolved_log=tmp_path / "resolved.jsonl",
    )


def log_task(client, task_id, model="bytedance/seedance-2", age_s=0):
    with open(client.TASK_LOG, "a") as f:
        f.write(
            json.dumps(
                {
                    "taskId": task_id,
                    "model": model,
                    "prompt": "p",
                    "createdAt": time.time() - age_s,
                }
            )
            + "\n"
        )


def test_finished_task_gets_downloaded_and_marked_resolved(client, monkeypatch):
    log_task(client, "t1")
    downloads = []
    monkeypatch.setattr(client, "check_task", lambda tid: ("success", ["http://x/f"]))
    monkeypatch.setattr(
        client, "download_urls", lambda urls, tid, ext: downloads.append((tid, ext))
    )

    recovered = client.resume_pending()

    assert recovered == [{"taskId": "t1", "outcome": "downloaded"}]
    assert downloads == [("t1", "mp4")]  # seedance model -> mp4
    # marked resolved: a second sweep never touches the API again
    calls = []
    monkeypatch.setattr(client, "check_task", lambda tid: calls.append(tid))
    assert client.resume_pending() == []
    assert calls == []


def test_failed_task_marked_resolved_and_never_rechecked(client, monkeypatch):
    log_task(client, "t2")
    monkeypatch.setattr(client, "check_task", lambda tid: ("fail", "credits gone"))

    assert client.resume_pending() == []

    calls = []
    monkeypatch.setattr(client, "check_task", lambda tid: calls.append(tid))
    client.resume_pending()
    assert calls == []


def test_still_generating_task_stays_pending_for_next_sweep(client, monkeypatch):
    log_task(client, "t3")
    monkeypatch.setattr(client, "check_task", lambda tid: ("generating", None))

    assert client.resume_pending() == []

    # NOT resolved: the next sweep checks it again
    calls = []
    monkeypatch.setattr(
        client, "check_task", lambda tid: (calls.append(tid), ("generating", None))[1]
    )
    client.resume_pending()
    assert calls == ["t3"]


def test_ancient_task_expires_instead_of_polling_forever(client, monkeypatch):
    log_task(client, "t4", age_s=KieAIClient.RESUME_MAX_AGE_S + 60)
    monkeypatch.setattr(
        client, "check_task", lambda tid: pytest.fail("expired task was checked")
    )

    assert client.resume_pending() == []
    assert "t4" in client._load_resolved()


def test_already_downloaded_file_short_circuits(client, monkeypatch):
    log_task(client, "t5")
    (client.OUT_DIR / "t5.mp4").write_bytes(b"already here")
    monkeypatch.setattr(
        client, "check_task", lambda tid: pytest.fail("downloaded task was checked")
    )

    assert client.resume_pending() == []
    assert "t5" in client._load_resolved()


def test_nanobanana_task_downloads_as_png(client, monkeypatch):
    log_task(client, "t6", model="nano-banana-pro")
    downloads = []
    monkeypatch.setattr(client, "check_task", lambda tid: ("success", ["http://x/f"]))
    monkeypatch.setattr(
        client, "download_urls", lambda urls, tid, ext: downloads.append(ext)
    )

    client.resume_pending()
    assert downloads == ["png"]


def test_download_failure_leaves_task_pending_for_retry(client, monkeypatch):
    log_task(client, "t7")
    monkeypatch.setattr(client, "check_task", lambda tid: ("success", ["http://x/f"]))

    def boom(urls, tid, ext):
        raise RuntimeError("url expired mid-download")

    monkeypatch.setattr(client, "download_urls", boom)
    assert client.resume_pending() == []
    assert "t7" not in client._load_resolved()  # retried next sweep


def test_sweeper_uses_only_configured_keys(monkeypatch):
    monkeypatch.setenv("KIE_AI_API_KEY_ADMIN", "key-a")
    monkeypatch.setenv("KIE_AI_API_KEY_VA", "key-a")  # duplicate collapses
    assert recovery._configured_keys() == ["key-a"]

    monkeypatch.setenv("KIE_AI_API_KEY_VA", "key-b")
    assert recovery._configured_keys() == ["key-a", "key-b"]

    monkeypatch.delenv("KIE_AI_API_KEY_ADMIN")
    monkeypatch.delenv("KIE_AI_API_KEY_VA")
    assert recovery._configured_keys() == []


def test_run_recovery_once_survives_a_broken_key(monkeypatch):
    monkeypatch.setenv("KIE_AI_API_KEY_ADMIN", "key-a")
    monkeypatch.setenv("KIE_AI_API_KEY_VA", "key-b")

    class FakeClient:
        calls = []

        def __init__(self, api_key):
            self.api_key = api_key

        def resume_pending(self):
            FakeClient.calls.append(self.api_key)
            if self.api_key == "key-a":
                raise RuntimeError("first key exploded")
            return [{"taskId": "x", "outcome": "downloaded"}]

    monkeypatch.setattr(
        recovery.KieAIClient,
        "from_env",
        classmethod(lambda cls, api_key: FakeClient(api_key)),
    )

    recovered = recovery.run_recovery_once()
    # first key's crash didn't stop the second key's sweep
    assert FakeClient.calls == ["key-a", "key-b"]
    assert recovered == [{"taskId": "x", "outcome": "downloaded"}]
