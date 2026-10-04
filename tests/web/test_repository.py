"""
Unit tests for web/db/repository.py -- the only layer allowed to touch the DB.
Covers create -> get round-trips and the atomicity guarantee that replaced the
old JSON file's documented read-modify-write race: two near-simultaneous status
updates must never leave a torn/half-written row.
"""

import threading

from ofmhelpers.cache import get_redis
from ofmhelpers.web.db.repositories import JobRepository
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
