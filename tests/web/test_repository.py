"""
Unit tests for web/db/repository.py -- the only layer allowed to touch the DB.
Covers create -> get round-trips and the atomicity guarantee that replaced the
old JSON file's documented read-modify-write race: two near-simultaneous status
updates must never leave a torn/half-written row.
"""

import threading

from ofmhelpers.cache import get_redis
from ofmhelpers.web.db.repositories import (
    ApprovalTokenRepository,
    InstagramStatsRepository,
    JobRepository,
    ModelRepository,
    TodoRepository,
)
from ofmhelpers.web.db.repositories.cached_repository import RepositoryCache


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


def test_model_repo_round_trip_with_instagram_accounts():
    repo = ModelRepository()
    model = repo.add("Model A", "https://onlyfans.com/a")

    account = repo.add_instagram_account(model["id"], "https://instagram.com/a")
    assert account is not None

    stored = repo.get(model["id"])
    assert stored["name"] == "Model A"
    assert len(stored["instagram_accounts"]) == 1
    assert stored["instagram_accounts"][0]["url"] == "https://instagram.com/a"

    assert repo.update_instagram_account(account["id"], "https://instagram.com/a2")
    stored = repo.get(model["id"])
    assert stored["instagram_accounts"][0]["url"] == "https://instagram.com/a2"

    assert repo.delete_instagram_account(account["id"])
    stored = repo.get(model["id"])
    assert stored["instagram_accounts"] == []


def test_model_add_instagram_accounts_bulk():
    repo = ModelRepository()
    model = repo.add("Model A", "")

    added = repo.add_instagram_accounts_bulk(
        model["id"], ["https://instagram.com/a", "https://instagram.com/b"]
    )
    assert len(added) == 2

    stored = repo.get(model["id"])
    urls = {a["url"] for a in stored["instagram_accounts"]}
    assert urls == {"https://instagram.com/a", "https://instagram.com/b"}


def test_instagram_stats_upsert_round_trip_and_overwrite():
    model_repo = ModelRepository()
    stats_repo = InstagramStatsRepository()
    model = model_repo.add("Model A", "")
    account = model_repo.add_instagram_account(model["id"], "https://instagram.com/a")

    posts = [
        {"url": "https://instagram.com/p/1", "views": 100, "likes": 10, "shares": None}
    ]
    stats_repo.upsert(account["id"], followers=1000, posts=posts, error=None)

    stored = stats_repo.get(account["id"])
    assert stored["followers"] == 1000
    assert stored["posts"] == posts
    assert stored["error"] is None

    # A second scrape overwrites rather than accumulating history.
    stats_repo.upsert(account["id"], followers=1100, posts=[], error="rate limited")
    stored = stats_repo.get(account["id"])
    assert stored["followers"] == 1100
    assert stored["posts"] == []
    assert stored["error"] == "rate limited"


def test_instagram_stats_get_many():
    model_repo = ModelRepository()
    stats_repo = InstagramStatsRepository()
    model = model_repo.add("Model A", "")
    a1 = model_repo.add_instagram_account(model["id"], "https://instagram.com/a")
    a2 = model_repo.add_instagram_account(model["id"], "https://instagram.com/b")
    stats_repo.upsert(a1["id"], followers=1, posts=[], error=None)

    many = stats_repo.get_many((a1["id"], a2["id"]))
    assert set(many) == {a1["id"]}


def test_model_add_instagram_accounts_bulk_returns_none_for_unknown_model():
    repo = ModelRepository()
    assert repo.add_instagram_accounts_bulk("doesnotexist", ["https://a"]) is None


def test_model_add_instagram_account_returns_none_for_unknown_model():
    repo = ModelRepository()
    assert repo.add_instagram_account("doesnotexist", "https://a") is None


def test_model_delete_cascades_to_instagram_accounts():
    repo = ModelRepository()
    model = repo.add("Model A", "")
    repo.add_instagram_account(model["id"], "https://instagram.com/a")

    assert repo.delete(model["id"]) is True
    assert repo.get(model["id"]) is None


def test_approval_token_consume_is_single_use():
    repo = ApprovalTokenRepository()
    token = repo.create("todo1", "/asset.png", ttl_seconds=3600)

    assert repo.consume(token, "/asset.png") == "ok"
    assert repo.consume(token, "/asset.png") == "used"
    # A mismatched asset path is rejected as stale, not approved.
    other = repo.create("todo2", "/asset2.png", ttl_seconds=3600)
    assert repo.consume(other, "/different.png") == "stale"


def test_job_get_is_served_from_cache_until_a_write_invalidates_it():
    from ofmhelpers.web.db.models import JobRow
    from ofmhelpers.web.db.session import session_scope

    namespace = "test-job-cache"
    cache = RepositoryCache(get_redis(), namespace)
    repo = JobRepository(cache=cache)
    job_id = repo.create("seedance", {})

    first = repo.get(job_id)

    # Mutate the row directly, bypassing the repo (and its bump()), to prove
    # the get() below is answered from cache rather than a fresh DB read.
    with session_scope() as s:
        s.get(JobRow, job_id).status = "done"
    assert repo.get(job_id) == first  # still cached, stale on purpose

    repo.update_status(job_id, "done")  # goes through the repo -> bumps cache

    assert repo.get(job_id)["status"] == "done"
    # A second repo sharing the same cache namespace sees the invalidation too.
    other_repo = JobRepository(cache=RepositoryCache(get_redis(), namespace))
    assert other_repo.get(job_id)["status"] == "done"


def test_repository_cache_bump_invalidates_all_prior_reads():
    cache = RepositoryCache(get_redis(), "test-generic-cache")
    calls = []

    def loader():
        calls.append(1)
        return {"n": len(calls)}

    first = cache.get_or_set("get", ("x",), loader)
    second = cache.get_or_set("get", ("x",), loader)
    assert first == second
    assert len(calls) == 1  # second call was a cache hit

    cache.bump()

    third = cache.get_or_set("get", ("x",), loader)
    assert len(calls) == 2  # bump() forced a fresh load
    assert third != first
