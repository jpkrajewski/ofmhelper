"""
Covers the "don't re-upload the same reference file to kie.ai every single
time" cache now that it lives in Redis (`ofmhelpers.cache`) instead of a
per-process LRU: KieAIClient.upload_local_file reads/writes
`kieai:upload:<api_key>:<path>`.

Real logs showed the same reference images/videos/audio getting re-uploaded
to kie.ai's tempfile host on every single generation, even when the exact
same local file (content-addressed in uploads/assets/) had already been
uploaded moments earlier -- pure wasted bandwidth/time. This is the fix,
and it's deliberately tested hard: a caching layer that silently serves a
stale or cross-account URL is worse than no cache at all.

Redis is flushed before every test by conftest's autouse `_clean_tables`, so
no test can leak a cached URL into the next one.
"""

from unittest import mock

import pytest
import requests
from redis.exceptions import RedisError

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.cache import get_redis, get_text
from ofmhelpers.config import settings


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


def _cached(api_key: str, path) -> str | None:
    return get_text(f"kieai:upload:{api_key}:{path}")


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
    assert _cached("test-key", ref_file) == "https://tempfile.example/x"


def test_cached_entry_carries_the_configured_ttl(client, ref_file):
    """A dead entry must age out on its own -- nothing else expires it."""
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response()
        client.upload_local_file(str(ref_file))

    ttl = get_redis().ttl(f"kieai:upload:test-key:{ref_file}")
    assert 0 < ttl <= settings.kieai.upload_cache_ttl_s


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


def test_a_second_client_with_the_same_key_reuses_the_cached_upload(tmp_path, ref_file):
    """The point of moving this to Redis: every KieAIClient instance, in this
    process or in the worker, sees the same answer."""
    kwargs = {
        "task_log": tmp_path / "t.jsonl",
        "completions_log": tmp_path / "c.jsonl",
        "resolved_log": tmp_path / "r.jsonl",
    }
    first_client = KieAIClient(api_key="k", out_dir=tmp_path / "a", **kwargs)
    second_client = KieAIClient(api_key="k", out_dir=tmp_path / "b", **kwargs)

    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        mreq.head.return_value = _mock_head_response(200)
        first = first_client.upload_local_file(str(ref_file))
        second = second_client.upload_local_file(str(ref_file))

    assert first == second == "https://tempfile.example/x"
    assert mreq.post.call_count == 1


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
    assert _cached("test-key", ref_file) == "https://tempfile.example/new"


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
    assert mreq.post.call_count == 2, (
        "a failed liveness check must fail closed, not trust the cache"
    )


def test_an_unreachable_broker_degrades_to_re_uploading(client, ref_file, monkeypatch):
    """Pure optimisation: a dead Redis must cost an upload, not a failure."""
    monkeypatch.setattr(
        "ofmhelpers.cache.redis.get_redis",
        mock.Mock(side_effect=RedisError("down")),
    )

    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response(url="https://tempfile.example/x")
        first = client.upload_local_file(str(ref_file))
        second = client.upload_local_file(str(ref_file))

    assert first == second == "https://tempfile.example/x"
    assert mreq.post.call_count == 2


def test_different_api_keys_never_share_a_cached_upload(tmp_path, ref_file):
    """kie.ai's tempfile host namespaces uploads per account; serving one
    account's URL for another account's key would hand out a cross-account
    reference."""
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
    assert mreq.post.call_count == 2, (
        "same file under two different keys must upload twice"
    )


def test_wrong_api_key_response_raises_and_never_populates_cache(client, ref_file):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value = _mock_post_response(success=False)
        with pytest.raises(Exception, match="Wrong API Key"):
            client.upload_local_file(str(ref_file))

    assert _cached("test-key", ref_file) is None


def test_http_error_on_upload_raises_and_never_populates_cache(client, ref_file):
    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        resp = mock.Mock()
        resp.raise_for_status.side_effect = requests.HTTPError("500 server error")
        mreq.post.return_value = resp

        with pytest.raises(requests.HTTPError):
            client.upload_local_file(str(ref_file))

    assert _cached("test-key", ref_file) is None


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
    assert _cached("test-key", ref_a) == "https://tempfile.example/a"
    assert _cached("test-key", ref_b) == "https://tempfile.example/b"


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
