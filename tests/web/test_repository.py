"""
Unit tests for web/db/repository.py -- the only layer allowed to touch the DB.
Covers create -> get round-trips and the atomicity guarantee that replaced the
old JSON file's documented read-modify-write race: two near-simultaneous status
updates must never leave a torn/half-written row.
"""

import threading

from ofmhelpers.web.db.repository import (
    ApprovalTokenRepository,
    JobRepository,
    TodoRepository,
)


def test_job_create_get_round_trip():
    repo = JobRepository()
    job_id = repo.create("seedance", {"prompt": "hi"}, actor="admin")

    job = repo.get(job_id)
    assert job["task"] == "seedance"
    assert job["params"] == {"prompt": "hi"}
    assert job["actor"] == "admin"
    assert job["status"] == "running"
    assert job["result"] is None


def test_update_status_is_atomic_under_concurrent_writers():
    """Ten threads each write a distinct result to the same job at once. Every
    write must succeed and the final row must equal exactly one writer's value
    -- never a partial mix (the race the lock-free JSON file couldn't guard)."""
    repo = JobRepository()
    job_id = repo.create("seedance", {})

    values = [[{"name": f"out{i}.mp4", "path": None}] for i in range(10)]
    errors: list[Exception] = []

    def writer(result):
        try:
            repo.update_status(job_id, "done", result=result)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(v,)) for v in values]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    final = repo.get(job_id)
    assert final["status"] == "done"
    assert final["result"] in values  # exactly one writer's value, intact


def test_update_status_failure_path_records_error_not_result():
    repo = JobRepository()
    job_id = repo.create("seedance", {})

    repo.update_status(job_id, "failed", error="Wrong API Key")

    job = repo.get(job_id)
    assert job["status"] == "failed"
    assert job["error"] == "Wrong API Key"
    assert job["result"] is None


def test_todo_repo_round_trip_and_asset_reset():
    repo = TodoRepository()
    todo = repo.add("Model A", "https://a", "notes", "admin")

    # Attaching an asset then a second asset resets prior approval/upload.
    repo.attach_asset(todo["id"], "/p1.png", "p1.png")
    assert repo.approve(todo["id"]) is True
    repo.attach_asset(todo["id"], "/p2.png", "p2.png")

    stored = repo.get(todo["id"])
    assert stored["asset_path"] == "/p2.png"
    assert stored["approved"] is False  # reset by the new asset


def test_approval_token_consume_is_single_use():
    repo = ApprovalTokenRepository()
    token = repo.create("todo1", "/asset.png", ttl_seconds=3600)

    assert repo.consume(token, "/asset.png") == "ok"
    assert repo.consume(token, "/asset.png") == "used"
    # A mismatched asset path is rejected as stale, not approved.
    other = repo.create("todo2", "/asset2.png", ttl_seconds=3600)
    assert repo.consume(other, "/different.png") == "stale"
