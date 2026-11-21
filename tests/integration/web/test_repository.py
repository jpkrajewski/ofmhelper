import os
import threading

from fastapi.testclient import TestClient
from redis import Redis
from signin import make_user, sign_in

from ofmhelpers.cache import get_redis
from ofmhelpers.db.cached_repository import RepositoryCache
from ofmhelpers.db.chats import store as chats
from ofmhelpers.db.chats.schema import ChatPlatform, ChatWebhook
from ofmhelpers.db.devices.repository import DeviceRepository
from ofmhelpers.db.drive_folders.repository import DriveFolderRepository
from ofmhelpers.db.jobs.repository import JobRepository
from ofmhelpers.db.models import JobRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.web.main import app


def test_job_create_get_round_trip():
    repo = JobRepository()
    admin = make_user("boss@example.com")
    job_id = repo.create("seedance", {"prompt": "hi"}, user_id=admin.id)

    job = repo.get(job_id)
    assert job.task == "seedance"
    assert job.params == {"prompt": "hi"}
    assert (job.user_id, job.actor) == (admin.id, str(admin.id))
    assert job.status == "running"
    assert job.result is None


def test_update_status_is_atomic_under_concurrent_writers():
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
    assert final.status == "done"
    assert final.result in values


def test_update_status_failure_path_records_error_not_result():
    repo = JobRepository()
    job_id = repo.create("seedance", {})

    repo.update_status(job_id, "failed", error="Wrong API Key")

    job = repo.get(job_id)
    assert job.status == "failed"
    assert job.error == "Wrong API Key"
    assert job.result is None


def test_job_get_is_served_from_cache_until_a_write_invalidates_it():
    namespace = "test-job-cache"
    cache = RepositoryCache(get_redis(), namespace)
    repo = JobRepository(cache=cache)
    job_id = repo.create("seedance", {})

    first = repo.get(job_id)

    with session_scope() as s:
        s.get(JobRow, job_id).status = "done"
    assert repo.get(job_id) == first

    repo.update_status(job_id, "done")

    assert repo.get(job_id).status == "done"
    other_repo = JobRepository(cache=RepositoryCache(get_redis(), namespace))
    assert other_repo.get(job_id).status == "done"


def test_repository_cache_bump_invalidates_all_prior_reads():
    cache = RepositoryCache(get_redis(), "test-generic-cache")
    calls = []

    def loader():
        calls.append(1)
        return {"n": len(calls)}

    first = cache.get_or_set("get", ("x",), loader)
    second = cache.get_or_set("get", ("x",), loader)
    assert first == second
    assert len(calls) == 1

    cache.bump()

    third = cache.get_or_set("get", ("x",), loader)
    assert len(calls) == 2
    assert third != first


def test_a_bump_of_a_namespace_it_depends_on_invalidates_the_cache():
    redis = get_redis()
    folders = RepositoryCache(redis, "test-folders", depends_on=("test-phones",))
    phones = RepositoryCache(redis, "test-phones")
    calls = []

    def loader():
        calls.append(1)
        return len(calls)

    folders.get_or_set("targets", (), loader)
    folders.get_or_set("targets", (), loader)
    phones.bump()
    folders.get_or_set("targets", (), loader)

    assert calls == [1, 1]


def test_a_logins_change_on_a_phone_drops_the_drive_targets_cache():
    folders = DriveFolderRepository()
    redis = get_redis()
    before = redis.get("cache:device:v")

    DeviceRepository().logins_changed()

    assert redis.get("cache:device:v") != before
    assert folders._cache._depends_on == ("device",)


def test_a_repositorys_cache_follows_the_redis_url_in_force(monkeypatch):
    suite_url = os.environ["OFM_REDIS_URL"]
    other_url = suite_url.rsplit("/", 1)[0] + "/14"
    monkeypatch.setenv("OFM_REDIS_URL", other_url)
    cache = RepositoryCache(None, "test-follows-url")
    assert cache.get_or_set("get", ("x",), lambda: "other") == "other"

    monkeypatch.setenv("OFM_REDIS_URL", suite_url)

    assert cache.get_or_set("get", ("x",), lambda: "suite") == "suite"
    other = Redis.from_url(other_url)
    other.delete(*other.scan_iter("cache:v2:test-follows-url:*"))


def _dead_redis() -> Redis:
    return Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)


def test_an_unreachable_cache_reads_and_writes_through_to_the_database():
    repo = JobRepository(cache=RepositoryCache(_dead_redis(), "job"))
    job_id = repo.create("seedance", {"prompt": "hi"})
    assert repo.get(job_id).status == "running"

    repo.update_status(job_id, "done", result=[])
    assert repo.get(job_id).status == "done"


def test_pages_answer_while_the_cache_is_down(monkeypatch):
    client = TestClient(app)
    sign_in(client, "ann@example.com")
    dead = _dead_redis()
    monkeypatch.setattr("ofmhelpers.db.cached_repository.get_redis", lambda: dead)
    assert client.get("/generate").status_code == 200
    assert client.get("/generate/gallery?hidden=1").status_code == 200


def test_free_text_arguments_never_collide_in_the_cache_key():
    cache = RepositoryCache(get_redis(), "test-key-encoding")

    first = cache.get_or_set("get", ("a:b", "c"), lambda: "one")
    second = cache.get_or_set("get", ("a", "b:c"), lambda: "two")

    assert (first, second) == ("one", "two")


def test_a_cache_key_never_reads_an_entry_of_the_old_key_format():
    redis = get_redis()
    cache = RepositoryCache(redis, "test-key-version")
    old_key = f"cache:test-key-version:{cache._version()}:get:x"
    redis.set(old_key, '"old"', ex=60)

    assert cache.get_or_set("get", ("x",), lambda: "new") == "new"
    redis.delete(old_key)


def test_an_empty_webhook_keep_list_deletes_no_row():
    hook = ChatWebhook(
        model_id="keep-empty", model_username="k", webhook_id="w", url="u", secret="s"
    )
    chats.save_webhook(ChatPlatform.STACKED, hook)

    assert chats.drop_webhooks_except(ChatPlatform.STACKED, []) == []
    assert hook in chats.webhooks(ChatPlatform.STACKED)
    chats.drop_webhooks_except(ChatPlatform.STACKED, ["nothing-else"])
