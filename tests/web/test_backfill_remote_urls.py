"""
Covers web/db/backfill_remote_urls.py: the one-time backfill that re-derives
remote_url (via kie.ai's recordInfo) for jobs generated before seedance.py/
kling.py/nbp.py started keeping it on a successful result.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from unittest import mock

import pytest

from ofmhelpers.web.db.backfill_remote_urls import extract_task_id, run
from ofmhelpers.web.db.repository import JobRepository
from ofmhelpers.web.stores.jobs import get_job

TASK_ID = "5a458500123456789abcdef012345678"


def test_extract_task_id_from_single_result_filename():
    assert extract_task_id(f"{TASK_ID}.mp4") == TASK_ID


def test_extract_task_id_from_multi_result_filename():
    assert extract_task_id(f"{TASK_ID}_0.mp4") == TASK_ID


def test_extract_task_id_returns_none_for_an_unrelated_filename():
    assert extract_task_id("abc123__myfile.mp4") is None


@pytest.fixture(autouse=True)
def _kie_key(monkeypatch):
    monkeypatch.setenv("KIE_AI_API_KEY_ADMIN", "k")
    monkeypatch.setenv("KIE_AI_API_KEY_VA", "")


def test_dry_run_reports_but_does_not_write(capsys):
    repo = JobRepository()
    job_id = repo.create("seedance", {"prompt": "p"}, status="done")
    repo.update_status(
        job_id,
        "done",
        result=[{"name": f"{TASK_ID}.mp4", "path": f"/app/kieai_out/{TASK_ID}.mp4"}],
    )

    with mock.patch("ofmhelpers.web.db.backfill_remote_urls.KieAIClient") as MockClient:
        MockClient.from_env.return_value.check_task.return_value = (
            "success",
            ["https://cdn.kie.ai/out/x.mp4"],
        )
        run(apply=False)

    assert get_job(job_id)["result"][0].get("remote_url") is None
    assert "dry-run" in capsys.readouterr().out


def test_apply_writes_remote_url_back_to_the_job():
    repo = JobRepository()
    job_id = repo.create("seedance", {"prompt": "p"}, status="done")
    repo.update_status(
        job_id,
        "done",
        result=[{"name": f"{TASK_ID}.mp4", "path": f"/app/kieai_out/{TASK_ID}.mp4"}],
    )

    with mock.patch("ofmhelpers.web.db.backfill_remote_urls.KieAIClient") as MockClient:
        MockClient.from_env.return_value.check_task.return_value = (
            "success",
            ["https://cdn.kie.ai/out/x.mp4"],
        )
        run(apply=True)

    assert get_job(job_id)["result"][0]["remote_url"] == "https://cdn.kie.ai/out/x.mp4"


def test_job_already_carrying_remote_url_is_left_alone():
    repo = JobRepository()
    job_id = repo.create("seedance", {"prompt": "p"}, status="done")
    repo.update_status(
        job_id,
        "done",
        result=[
            {
                "name": f"{TASK_ID}.mp4",
                "path": f"/app/kieai_out/{TASK_ID}.mp4",
                "remote_url": "https://cdn.kie.ai/out/already-there.mp4",
            }
        ],
    )

    with mock.patch("ofmhelpers.web.db.backfill_remote_urls.KieAIClient") as MockClient:
        run(apply=True)
        MockClient.from_env.return_value.check_task.assert_not_called()

    assert (
        get_job(job_id)["result"][0]["remote_url"]
        == "https://cdn.kie.ai/out/already-there.mp4"
    )


def test_expired_task_is_skipped_without_writing():
    repo = JobRepository()
    job_id = repo.create("seedance", {"prompt": "p"}, status="done")
    repo.update_status(
        job_id,
        "done",
        result=[{"name": f"{TASK_ID}.mp4", "path": f"/app/kieai_out/{TASK_ID}.mp4"}],
    )

    with mock.patch("ofmhelpers.web.db.backfill_remote_urls.KieAIClient") as MockClient:
        MockClient.from_env.return_value.check_task.return_value = (
            "unknown",
            "task not found",
        )
        run(apply=True)

    assert get_job(job_id)["result"][0].get("remote_url") is None
