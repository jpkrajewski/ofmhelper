"""
Covers the in-memory "don't re-upload the same reference file to kie.ai
every single time" cache: the standalone UploadCache LRU (upload_cache.py)
on its own, and its integration into KieAIClient.upload_local_file.

Real logs showed the same reference images/videos/audio getting re-uploaded
to kie.ai's tempfile host on every single generation, even when the exact
same local file (content-addressed in uploads/assets/) had already been
uploaded moments earlier -- pure wasted bandwidth/time. This is the fix,
and it's deliberately tested hard: a caching layer that silently serves a
stale or cross-account URL is worse than no cache at all.
"""

import threading
import unittest.mock as mock

import pytest
import requests

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.upload_cache import UploadCache, upload_cache


# ======================================================================
# UploadCache: the standalone LRU, no network involved
# ======================================================================


def test_get_on_empty_cache_returns_none():
    cache = UploadCache()
    assert cache.get("key", "/path/a.png") is None


def test_put_then_get_roundtrips():
    cache = UploadCache()
    cache.put("key", "/path/a.png", "https://example.com/a.png")
    assert cache.get("key", "/path/a.png") == "https://example.com/a.png"


def test_get_is_scoped_by_api_key():
    """Same local path, different API key -> must NOT hit. kie.ai's tempfile
    host namespaces uploads per account; serving one account's URL for
    another account's key would hand out a cross-account reference."""
    cache = UploadCache()
    cache.put("key-a", "/path/a.png", "https://example.com/a.png")
    assert cache.get("key-b", "/path/a.png") is None


def test_get_is_scoped_by_path():
    cache = UploadCache()
    cache.put("key", "/path/a.png", "https://example.com/a.png")
    assert cache.get("key", "/path/b.png") is None


def test_discard_removes_entry():
    cache = UploadCache()
    cache.put("key", "/path/a.png", "https://example.com/a.png")
    cache.discard("key", "/path/a.png")
    assert cache.get("key", "/path/a.png") is None


def test_discard_missing_entry_is_a_noop():
    cache = UploadCache()
    cache.discard("key", "/path/nope.png")  # must not raise
    assert len(cache) == 0


def test_clear_empties_cache():
    cache = UploadCache()
    cache.put("key", "/path/a.png", "https://example.com/a.png")
    cache.put("key", "/path/b.png", "https://example.com/b.png")
    cache.clear()
    assert len(cache) == 0
    assert cache.get("key", "/path/a.png") is None


def test_len_reflects_entry_count():
    cache = UploadCache()
    assert len(cache) == 0
    cache.put("key", "/path/a.png", "u1")
    assert len(cache) == 1
    cache.put("key", "/path/b.png", "u2")
    assert len(cache) == 2


def test_overwriting_existing_key_does_not_grow_cache():
    cache = UploadCache()
    cache.put("key", "/path/a.png", "https://example.com/a-old.png")
    cache.put("key", "/path/a.png", "https://example.com/a-new.png")
    assert len(cache) == 1
    assert cache.get("key", "/path/a.png") == "https://example.com/a-new.png"


def test_eviction_when_over_capacity():
    cache = UploadCache(max_entries=3)
    cache.put("key", "/1", "u1")
    cache.put("key", "/2", "u2")
    cache.put("key", "/3", "u3")
    cache.put("key", "/4", "u4")  # pushes cache over capacity

    assert len(cache) == 3
    assert cache.get("key", "/1") is None  # oldest, evicted
    assert cache.get("key", "/2") == "u2"
    assert cache.get("key", "/3") == "u3"
    assert cache.get("key", "/4") == "u4"


def test_get_refreshes_recency_preventing_eviction():
    cache = UploadCache(max_entries=3)
    cache.put("key", "/1", "u1")
    cache.put("key", "/2", "u2")
    cache.put("key", "/3", "u3")

    cache.get("key", "/1")  # touch /1 -- now the most-recently-used

    cache.put("key", "/4", "u4")  # must evict /2 (now the oldest), not /1

    assert cache.get("key", "/1") == "u1"
    assert cache.get("key", "/2") is None
    assert cache.get("key", "/3") == "u3"
    assert cache.get("key", "/4") == "u4"


def test_put_also_refreshes_recency():
    cache = UploadCache(max_entries=3)
    cache.put("key", "/1", "u1")
    cache.put("key", "/2", "u2")
    cache.put("key", "/3", "u3")

    cache.put("key", "/1", "u1-updated")  # re-put -- also most-recently-used

    cache.put("key", "/4", "u4")  # must evict /2, not /1

    assert cache.get("key", "/1") == "u1-updated"
    assert cache.get("key", "/2") is None


def test_max_entries_must_be_positive():
    with pytest.raises(ValueError):
        UploadCache(max_entries=0)
    with pytest.raises(ValueError):
        UploadCache(max_entries=-1)


def test_default_capacity_is_100():
    cache = UploadCache()
    for i in range(150):
        cache.put("key", f"/{i}", f"u{i}")
    assert len(cache) == 100
    assert cache.get("key", "/0") is None  # long since evicted
    assert cache.get("key", "/149") == "u149"


def test_module_level_singleton_is_capped_at_100():
    """The shared instance every KieAIClient uses -- same capacity contract
    as a fresh UploadCache."""
    upload_cache.clear()
    try:
        for i in range(120):
            upload_cache.put("key", f"/{i}", f"u{i}")
        assert len(upload_cache) == 100
    finally:
        upload_cache.clear()


def test_concurrent_puts_do_not_corrupt_the_cache():
    """Best-effort concurrency check -- many threads hammering the same
    cache must never leave it over capacity or raise."""
    cache = UploadCache(max_entries=20)

    def worker(n):
        for i in range(50):
            cache.put("key", f"/{n}-{i}", f"u{n}-{i}")
            cache.get("key", f"/{n}-{i}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(cache) == 20


# ======================================================================
# KieAIClient.upload_local_file: cache wired into the real upload path
# ======================================================================


@pytest.fixture(autouse=True)
def _clean_shared_cache():
    """The module-level `upload_cache` is a process-wide singleton --
    every test must start and end with it empty so tests can't leak
    cached URLs into each other."""
    upload_cache.clear()
    yield
    upload_cache.clear()


@pytest.fixture
def client(tmp_path):
    return KieAIClient(
        api_key="test-key",
        out_dir=tmp_path / "out",
        task_log=tmp_path / "tasks.jsonl",
        completions_log=tmp_path / "completions.jsonl",
        resolved_log=tmp_path / "resolved.jsonl",
    )


@pytest.fixture
def ref_file(tmp_path):
    p = tmp_path / "ref.png"
    p.write_bytes(b"fake image bytes")
    return p


def _mock_post_response(url="https://tempfile.example/refs/ref.png", success=True):
    resp = mock.Mock()
    resp.raise_for_status = mock.Mock()
    resp.json.return_value = {"success": success, "data": {"downloadUrl": url}}
    return resp


def _mock_head_response(status_code=200):
    resp = mock.Mock()
    resp.status_code = status_code
    return resp


def test_first_upload_hits_network_and_populates_cache(client, ref_file):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        url = client.upload_local_file(str(ref_file))

    assert url == "https://tempfile.example/x"
    assert mreq.post.call_count == 1
    assert upload_cache.get("test-key", str(ref_file)) == "https://tempfile.example/x"


def test_second_upload_is_served_from_cache_when_remote_confirms_alive(
    client, ref_file
):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        first = client.upload_local_file(str(ref_file))

        mreq.head.return_value = _mock_head_response(200)
        second = client.upload_local_file(str(ref_file))

    assert first == second == "https://tempfile.example/x"
    assert mreq.post.call_count == 1, "second call must not re-upload"
    assert mreq.head.call_count == 1


def test_repeated_uploads_stay_cached_across_many_calls(client, ref_file):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        mreq.head.return_value = _mock_head_response(200)
        urls = [client.upload_local_file(str(ref_file)) for _ in range(5)]

    assert len(set(urls)) == 1
    assert mreq.post.call_count == 1


def test_cache_hit_but_remote_gone_triggers_reupload(client, ref_file):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/old")
        client.upload_local_file(str(ref_file))

        mreq.head.return_value = _mock_head_response(404)  # expired on kie.ai's side
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/new")
        second = client.upload_local_file(str(ref_file))

    assert second == "https://tempfile.example/new"
    assert mreq.post.call_count == 2, "a dead cached url must trigger a fresh upload"
    assert upload_cache.get("test-key", str(ref_file)) == "https://tempfile.example/new"


def test_remote_check_network_error_triggers_reupload(client, ref_file):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        # requests itself is mocked, so `except requests.RequestException`
        # inside _remote_file_exists needs the REAL class here -- otherwise
        # it's an auto-generated Mock attribute and Python can't match a
        # raised exception against it (TypeError: catching classes that do
        # not inherit from BaseException).
        mreq.RequestException = requests.RequestException

        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/old")
        client.upload_local_file(str(ref_file))

        mreq.head.side_effect = requests.ConnectionError("boom")
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/new")
        second = client.upload_local_file(str(ref_file))

    assert second == "https://tempfile.example/new"
    assert (
        mreq.post.call_count == 2
    ), "a failed liveness check must fail closed, not trust the cache"


def test_different_api_keys_never_share_a_cached_upload(tmp_path, ref_file):
    client_a = KieAIClient(
        api_key="key-a",
        out_dir=tmp_path / "a",
        task_log=tmp_path / "a.jsonl",
        completions_log=tmp_path / "a-c.jsonl",
        resolved_log=tmp_path / "a-r.jsonl",
    )
    client_b = KieAIClient(
        api_key="key-b",
        out_dir=tmp_path / "b",
        task_log=tmp_path / "b.jsonl",
        completions_log=tmp_path / "b-c.jsonl",
        resolved_log=tmp_path / "b-r.jsonl",
    )

    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.side_effect = [
            _mock_post_response(url="https://tempfile.example/for-a"),
            _mock_post_response(url="https://tempfile.example/for-b"),
        ]
        url_a = client_a.upload_local_file(str(ref_file))
        url_b = client_b.upload_local_file(str(ref_file))

    assert url_a == "https://tempfile.example/for-a"
    assert url_b == "https://tempfile.example/for-b"
    assert (
        mreq.post.call_count == 2
    ), "same file under two different keys must upload twice"


def test_wrong_api_key_response_raises_and_never_populates_cache(client, ref_file):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response(success=False)
        with pytest.raises(Exception, match="Wrong API Key"):
            client.upload_local_file(str(ref_file))

    assert upload_cache.get("test-key", str(ref_file)) is None


def test_http_error_on_upload_raises_and_never_populates_cache(client, ref_file):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        resp = mock.Mock()
        resp.raise_for_status.side_effect = requests.HTTPError("500 server error")
        mreq.post.return_value = resp

        with pytest.raises(requests.HTTPError):
            client.upload_local_file(str(ref_file))

    assert upload_cache.get("test-key", str(ref_file)) is None


def test_two_different_local_files_cache_independently(client, tmp_path):
    ref_a = tmp_path / "a.png"
    ref_a.write_bytes(b"a")
    ref_b = tmp_path / "b.png"
    ref_b.write_bytes(b"b")

    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.side_effect = [
            _mock_post_response(url="https://tempfile.example/a"),
            _mock_post_response(url="https://tempfile.example/b"),
        ]
        url_a = client.upload_local_file(str(ref_a))
        url_b = client.upload_local_file(str(ref_b))

    assert url_a != url_b
    assert mreq.post.call_count == 2
    assert upload_cache.get("test-key", str(ref_a)) == "https://tempfile.example/a"
    assert upload_cache.get("test-key", str(ref_b)) == "https://tempfile.example/b"


def test_upload_path_argument_still_forwarded_on_a_fresh_upload(client, ref_file):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response()
        client.upload_local_file(str(ref_file), upload_path="custom-dir")

    _, kwargs = mreq.post.call_args
    assert kwargs["data"]["uploadPath"] == "custom-dir"


def test_remote_file_exists_head_4xx_is_treated_as_missing(client):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.head.return_value = _mock_head_response(410)
        assert client._remote_file_exists("https://tempfile.example/gone") is False


def test_remote_file_exists_head_2xx_is_treated_as_present(client):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.head.return_value = _mock_head_response(200)
        assert (
            client._remote_file_exists("https://tempfile.example/still-there") is True
        )


def test_remote_file_exists_swallows_request_exceptions(client):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.RequestException = requests.RequestException
        mreq.head.side_effect = requests.Timeout("slow host")
        assert client._remote_file_exists("https://tempfile.example/x") is False
