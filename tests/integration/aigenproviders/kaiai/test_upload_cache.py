import threading
import time
from unittest import mock

import pytest
import requests
from kie_http_fake import kie_http, response
from PIL import Image
from redis import Redis

from ofmhelpers.cache import get_redis, get_text
from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderAuthError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.ai_providers.kie.client import KieAIClient


@pytest.fixture
def client(tmp_path):
    return KieAIClient(
        api_key="test-key",
        out_dir=tmp_path / "out",
    )


@pytest.fixture
def ref_file(tmp_path):
    p = tmp_path / "ref.png"
    Image.new("RGB", (8, 8)).save(p)
    return p


def _key(client: KieAIClient, path) -> str:
    return client._upload_cache_key(str(path))


def _cached(client: KieAIClient, path) -> str | None:
    return get_text(_key(client, path))


def _mock_post_response(url="https://tempfile.example/refs/ref.png", success=True):
    return response({"success": success, "data": {"downloadUrl": url}})


def _mock_head_response(status_code=200):
    resp = mock.Mock()
    resp.status_code = status_code
    return resp


def test_first_upload_hits_network_and_populates_cache(client, ref_file):
    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        url = client.upload_local_file(str(ref_file))

    assert url == "https://tempfile.example/x"
    assert mreq.post.call_count == 1
    assert _cached(client, ref_file) == "https://tempfile.example/x"


def test_cached_entry_carries_the_configured_ttl(client, ref_file):
    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response()
        client.upload_local_file(str(ref_file))

    ttl = get_redis().ttl(_key(client, ref_file))
    assert 0 < ttl <= settings.kieai.upload_cache_ttl_s


def test_the_api_key_is_never_part_of_a_redis_key(client, ref_file):
    seen = []

    def post(*_args, **_kwargs):
        seen.extend(get_redis().scan_iter("*"))
        return _mock_post_response()

    with kie_http() as mreq:
        mreq.post.side_effect = post
        client.upload_local_file(str(ref_file))
    seen.extend(get_redis().scan_iter("*"))

    assert f"lock:{_key(client, ref_file)}".encode() in seen
    assert _key(client, ref_file).encode() in seen
    assert not [k for k in seen if b"test-key" in k]


def test_second_upload_is_served_from_cache_when_remote_confirms_alive(
    client, ref_file
):
    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        first = client.upload_local_file(str(ref_file))

        mreq.head.return_value = _mock_head_response(200)
        second = client.upload_local_file(str(ref_file))

    assert first == second == "https://tempfile.example/x"
    assert mreq.post.call_count == 1, "second call must not re-upload"
    assert mreq.head.call_count == 1


def test_a_second_client_with_the_same_key_reuses_the_cached_upload(tmp_path, ref_file):
    first_client = KieAIClient(api_key="k", out_dir=tmp_path / "a")
    second_client = KieAIClient(api_key="k", out_dir=tmp_path / "b")

    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        mreq.head.return_value = _mock_head_response(200)
        first = first_client.upload_local_file(str(ref_file))
        second = second_client.upload_local_file(str(ref_file))

    assert first == second == "https://tempfile.example/x"
    assert mreq.post.call_count == 1


def test_repeated_uploads_stay_cached_across_many_calls(client, ref_file):
    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        mreq.head.return_value = _mock_head_response(200)
        urls = [client.upload_local_file(str(ref_file)) for _ in range(5)]

    assert len(set(urls)) == 1
    assert mreq.post.call_count == 1


def test_cache_hit_but_remote_gone_triggers_reupload(client, ref_file):
    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/old")
        client.upload_local_file(str(ref_file))

        mreq.head.return_value = _mock_head_response(404)
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/new")
        second = client.upload_local_file(str(ref_file))

    assert second == "https://tempfile.example/new"
    assert mreq.post.call_count == 2, "a dead cached url must trigger a fresh upload"
    assert _cached(client, ref_file) == "https://tempfile.example/new"


def test_remote_check_network_error_triggers_reupload(client, ref_file):
    with kie_http() as mreq:
        mreq.RequestException = requests.RequestException

        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/old")
        client.upload_local_file(str(ref_file))

        mreq.head.side_effect = requests.ConnectionError("boom")
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/new")
        second = client.upload_local_file(str(ref_file))

    assert second == "https://tempfile.example/new"
    assert mreq.post.call_count == 2, (
        "a failed liveness check must fail closed, not trust the cache"
    )


def test_an_unreachable_broker_degrades_to_re_uploading(
    client, ref_file, monkeypatch, caplog
):
    dead = Redis.from_url("unix:///nonexistent/redis.sock")
    monkeypatch.setattr("ofmhelpers.cache.redis.get_redis", lambda: dead)
    monkeypatch.setattr(
        "ofmhelpers.integrations.ai_providers.base.get_redis", lambda: dead
    )

    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        first = client.upload_local_file(str(ref_file))
        second = client.upload_local_file(str(ref_file))

    assert first == second == "https://tempfile.example/x"
    assert mreq.post.call_count == 2
    assert caplog.text.count("upload lock unavailable") == 2
    assert "test-key" not in caplog.text


def test_a_lock_lost_mid_upload_is_swallowed(client, ref_file, caplog):
    lock_key = f"lock:{_key(client, ref_file)}"

    def post(*_args, **_kwargs):
        assert get_redis().delete(lock_key) == 1, "the upload holds the lock"
        return _mock_post_response(url="https://tempfile.example/x")

    with kie_http() as mreq:
        mreq.post.side_effect = post
        url = client.upload_local_file(str(ref_file))

    assert url == "https://tempfile.example/x"
    assert _cached(client, ref_file) == url
    assert "not released" in caplog.text


def test_a_lock_left_by_a_dead_worker_only_delays_the_upload(client, ref_file):
    get_redis().set(f"lock:{_key(client, ref_file)}", "dead-worker", px=300)
    started = time.monotonic()

    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        url = client.upload_local_file(str(ref_file))

    assert url == "https://tempfile.example/x"
    assert mreq.post.call_count == 1
    assert time.monotonic() - started >= 0.25


def test_different_api_keys_never_share_a_cached_upload(tmp_path, ref_file):
    client_a = KieAIClient(
        api_key="key-a",
        out_dir=tmp_path / "a",
    )
    client_b = KieAIClient(
        api_key="key-b",
        out_dir=tmp_path / "b",
    )

    with kie_http() as mreq:
        mreq.post.side_effect = [
            _mock_post_response(url="https://tempfile.example/for-a"),
            _mock_post_response(url="https://tempfile.example/for-b"),
        ]
        url_a = client_a.upload_local_file(str(ref_file))
        url_b = client_b.upload_local_file(str(ref_file))

    assert url_a == "https://tempfile.example/for-a"
    assert url_b == "https://tempfile.example/for-b"
    assert mreq.post.call_count == 2, (
        "same file under two different keys must upload twice"
    )


def test_wrong_api_key_response_raises_and_never_populates_cache(client, ref_file):
    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response(success=False)
        with pytest.raises(ProviderAuthError) as exc:
            client.upload_local_file(str(ref_file))

    assert str(exc.value) == "kie.ai: wrong API key"
    assert "upload rejected" in exc.value.detail

    assert _cached(client, ref_file) is None


def test_http_error_on_upload_raises_and_never_populates_cache(client, ref_file):
    with kie_http() as mreq:
        mreq.post.return_value = response({}, status_code=500)

        with pytest.raises(ProviderUnavailableError):
            client.upload_local_file(str(ref_file))

    assert _cached(client, ref_file) is None


def test_two_different_local_files_cache_independently(client, tmp_path):
    ref_a = tmp_path / "a.png"
    Image.new("RGB", (8, 8), "red").save(ref_a)
    ref_b = tmp_path / "b.png"
    Image.new("RGB", (8, 8), "blue").save(ref_b)

    with kie_http() as mreq:
        mreq.post.side_effect = [
            _mock_post_response(url="https://tempfile.example/a"),
            _mock_post_response(url="https://tempfile.example/b"),
        ]
        url_a = client.upload_local_file(str(ref_a))
        url_b = client.upload_local_file(str(ref_b))

    assert url_a != url_b
    assert mreq.post.call_count == 2
    assert _cached(client, ref_a) == "https://tempfile.example/a"
    assert _cached(client, ref_b) == "https://tempfile.example/b"


def test_upload_path_argument_still_forwarded_on_a_fresh_upload(client, ref_file):
    with kie_http() as mreq:
        mreq.post.return_value = _mock_post_response()
        client.upload_local_file(str(ref_file), upload_path="custom-dir")

    _, kwargs = mreq.post.call_args
    assert kwargs["data"]["uploadPath"] == "custom-dir"


def test_remote_file_exists_head_4xx_is_treated_as_missing(client):
    with kie_http() as mreq:
        mreq.head.return_value = _mock_head_response(410)
        assert client._remote_file_exists("https://tempfile.example/gone") is False


def test_remote_file_exists_head_2xx_is_treated_as_present(client):
    with kie_http() as mreq:
        mreq.head.return_value = _mock_head_response(200)
        assert (
            client._remote_file_exists("https://tempfile.example/still-there") is True
        )


def test_remote_file_exists_swallows_request_exceptions(client):
    with kie_http() as mreq:
        mreq.RequestException = requests.RequestException
        mreq.head.side_effect = requests.Timeout("slow host")
        assert client._remote_file_exists("https://tempfile.example/x") is False


def test_one_file_asked_for_at_once_is_uploaded_once(tmp_path, ref_file):

    def new_client():
        return KieAIClient(
            api_key="test-key",
            out_dir=tmp_path / "out",
        )

    lock_key = f"lock:{_key(new_client(), ref_file)}"
    held_in_redis = []

    def slow_post(*_args, **_kwargs):
        held_in_redis.append(bool(get_redis().exists(lock_key)))
        time.sleep(0.2)
        return _mock_post_response(url="https://tempfile.example/once")

    urls = []
    with kie_http() as mreq:
        mreq.post.side_effect = slow_post
        mreq.head.return_value = _mock_head_response(200)
        threads = [
            threading.Thread(
                target=lambda: urls.append(
                    new_client().upload_local_file(str(ref_file))
                )
            )
            for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert urls == ["https://tempfile.example/once"] * 3
    assert mreq.post.call_count == 1
    assert held_in_redis == [True]
    assert not get_redis().exists(lock_key), "released after the upload"
