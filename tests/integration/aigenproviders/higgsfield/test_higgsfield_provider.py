"""Higgsfield behind the shared interface, on the real SDK with its HTTP faked
at the wire (`httpx.Client.request`, which every SDK call goes through): the
auth header, the submit, the six statuses, the two-step upload and how
failures map to error types."""

import threading
import time
from unittest import mock

import httpx
import pytest
from PIL import Image

from ofmhelpers.aigenproviders import tasks
from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.higgsfield.fake import FAKE_KEY, fake_higgsfield
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider
from ofmhelpers.aigenproviders.runner import generate
from ofmhelpers.cache import get_redis
from ofmhelpers.web.schemas.errors import error_reason

BASE = "https://api.higgsfield.ai"
SPEC = get_spec("higgsfield:alibaba/wan-3.0/reference-to-video")
SUBMIT = ("POST", f"/{SPEC.model_id}")
QUEUED = {
    "status": "queued",
    "request_id": "r1",
    "status_url": f"{BASE}/requests/r1/status",
    "cancel_url": f"{BASE}/requests/r1/cancel",
}


class Wire:
    """Answers each (method, path) from a table -- `(status, body)` or
    `(status, body, headers)`, a bytes body sent as is; records every call
    with the headers the client sent."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, client, method, url, **kwargs):
        full = (
            httpx.URL(str(client.base_url).rstrip("/") + "/" + str(url).lstrip("/"))
            if not str(url).startswith("http")
            else httpx.URL(url)
        )
        sent = httpx.Headers(client.headers)
        sent.update(kwargs.pop("headers", None) or {})
        self.calls.append(
            {"method": method, "url": str(full), "headers": dict(sent), **kwargs}
        )
        answer = self.routes[(method, full.path)]
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer()
        status, body, *headers = answer
        content = {"content": body} if isinstance(body, bytes) else {"json": body}
        return httpx.Response(
            status,
            headers=headers[0] if headers else None,
            request=httpx.Request(method, full),
            **content,
        )


@pytest.fixture
def wire(monkeypatch):
    def install(routes):
        fake = Wire(routes)

        def request(client, method, url, **kwargs):
            # A method, so the fake sees which client (headers) called it.
            return fake(client, method, url, **kwargs)

        monkeypatch.setattr(httpx.Client, "request", request)
        return fake

    return install


@pytest.fixture
def provider():
    return HiggsfieldProvider("key-id:key-secret")


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)


def test_submit_posts_the_payload_to_the_model_with_the_key(wire, provider):
    fake = wire({SUBMIT: (200, QUEUED)})

    assert provider.submit(SPEC, {"prompt": "p"}, idempotency_key="job") == "r1"
    [call] = fake.calls
    assert call["url"] == f"{BASE}/{SPEC.model_id}"
    assert call["json"] == {"prompt": "p"}
    assert call["headers"]["authorization"] == "Key key-id:key-secret"
    assert call["headers"]["idempotency-key"].startswith("job-")


def _submit_keys(wire, provider, submits):
    fake = wire({SUBMIT: (200, QUEUED)})
    for job, payload in submits:
        provider.submit(SPEC, payload, idempotency_key=job)
    return [c["headers"]["idempotency-key"] for c in fake.calls]


def test_the_idempotency_key_is_one_per_job_and_request(wire, provider):
    """A resend of the same request reuses it; another job or changed inputs
    (Higgsfield answers 422 to a key reused with other inputs) get their own."""
    same, again, changed, other_job = _submit_keys(
        wire,
        provider,
        [
            ("job", {"prompt": "a", "seed": 1}),
            ("job", {"seed": 1, "prompt": "a"}),
            ("job", {"prompt": "b", "seed": 1}),
            ("job2", {"prompt": "a", "seed": 1}),
        ],
    )

    assert same == again
    assert len({same, changed, other_job}) == 3
    # docs: 1-255 visible ASCII characters, no whitespace.
    assert len(same) <= 255
    assert same.isascii()
    assert same.isprintable()
    assert " " not in same


def test_a_resent_submit_gets_the_request_it_made(fake_externals):
    """The fake keeps Higgsfield's documented replay: same key, same request,
    nothing new made (or paid)."""
    first = HiggsfieldProvider(FAKE_KEY).submit(SPEC, {"prompt": "a"}, "job-1")
    again = HiggsfieldProvider(FAKE_KEY).submit(SPEC, {"prompt": "a"}, "job-1")
    other = HiggsfieldProvider(FAKE_KEY).submit(SPEC, {"prompt": "a"}, "job-2")

    assert first == again != other
    assert fake_higgsfield().submitted == [first, other]


def _status_routes(body):
    return {("GET", "/requests/r1/status"): (200, body)}


@pytest.mark.parametrize("status", ["queued", "in_progress"])
def test_in_flight_is_pending(wire, provider, status):
    wire(_status_routes({"status": status, "request_id": "r1"}))

    assert provider.status("r1").state == "pending"


@pytest.mark.parametrize(
    ("body", "urls"),
    [
        ({"video": {"url": "https://cdn/v.mp4"}}, ["https://cdn/v.mp4"]),
        (
            {"images": [{"url": "https://cdn/a.png"}, {"url": "https://cdn/b.png"}]},
            ["https://cdn/a.png", "https://cdn/b.png"],
        ),
    ],
)
def test_completed_returns_every_output_url(wire, provider, body, urls):
    wire(_status_routes({"status": "completed", "request_id": "r1", **body}))

    state = provider.status("r1")

    assert (state.state, state.urls) == ("done", urls)


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"status": "failed", "error": "bad input video"}, "bad input video"),
        ({"status": "nsfw"}, "content moderation"),
        ({"status": "canceled"}, "canceled"),
    ],
)
def test_ended_without_output_is_failed_with_a_reason(wire, provider, body, error):
    wire(_status_routes(body))

    state = provider.status("r1")

    assert state.state == "failed"
    assert error in state.error


@pytest.mark.parametrize(
    "body",
    [
        {"status": "moderating", "request_id": "r1"},
        b"<html>502 Bad Gateway</html>",
        {"status": "completed", "request_id": "r1", "error": {"code": 1}},
    ],
    ids=["unknown-status", "not-json", "unexpected-shape"],
)
def test_an_unreadable_status_is_retryable(wire, provider, body):
    """2026-10-25 review #56: a ValueError here failed the run as not
    resumable and forgot a request that was already paid."""
    wire(_status_routes(body))

    with pytest.raises(ProviderUnavailableError, match="unreadable answer"):
        provider.status("r1")


def test_an_unreadable_status_keeps_the_paid_request_for_recovery(
    wire, provider, monkeypatch
):
    monkeypatch.setenv("OFM_AIGEN_VIDEO_POLL_TIMEOUT_S", "1")
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0.05")
    monkeypatch.setenv("OFM_AIGEN_POLL_MAX_INTERVAL_S", "0.1")
    fake = wire({SUBMIT: (200, QUEUED), **_status_routes({"status": "moderating"})})

    with pytest.raises(ProviderTimeoutError):
        generate(provider, SPEC, {"prompt": "a"}, "job-1")

    assert [c["method"] for c in fake.calls].count("POST") == 1
    assert [c["method"] for c in fake.calls].count("GET") >= 1
    assert tasks.recall("job-1").task_id == "r1"


@pytest.mark.parametrize(
    ("status", "headers", "error"),
    [
        (401, {}, ProviderAuthError),
        (403, {"Cache-Control": "no-store"}, ProviderAuthError),
        (403, {}, ProviderNoCreditsError),
        (402, {}, ProviderNoCreditsError),
        (422, {}, ProviderInputError),
    ],
    ids=["401", "403-disabled-key", "403-balance", "402", "422"],
)
def test_http_errors_map_to_error_types(wire, provider, status, headers, error):
    """docs.higgsfield.ai concepts/errors: 403 is "Insufficient balance";
    api-reference/headers: a disabled or expired key's 403 is `no-store`."""
    wire({SUBMIT: (status, {"detail": "nope"}, headers)})

    with pytest.raises(error) as raised:
        provider.submit(SPEC, {}, "job")

    assert type(raised.value) is error
    assert "nope" in str(raised.value) + getattr(raised.value, "detail", "")


@pytest.mark.parametrize("status", [402, 403])
def test_no_balance_shows_no_credits_on_the_card(wire, provider, status):
    wire({SUBMIT: (status, {"detail": "Not enough credits"})})

    with pytest.raises(ProviderNoCreditsError) as raised:
        provider.submit(SPEC, {}, "job")

    assert error_reason(str(raised.value)) == "No credits"


def test_a_rejected_key_says_so_in_one_line(wire, provider):
    wire({SUBMIT: (401, {"detail": "Invalid credentials"})})

    with pytest.raises(ProviderAuthError) as raised:
        provider.submit(SPEC, {}, "job")

    assert str(raised.value) == "Higgsfield: wrong API key"
    assert "Invalid credentials" in raised.value.detail


QUEUE_FULL = {
    "detail": {
        "code": "account_queue_full",
        "message": "Too many queued generations for this account",
        "retryable": True,
        "limit": 40,
        "retry_after_seconds": 30,
    }
}


@pytest.mark.parametrize(
    ("body", "headers", "wait"),
    [
        (QUEUE_FULL, {"Retry-After": "12"}, 12.0),
        (QUEUE_FULL, {}, 30.0),
        ({"detail": "Maximum number of concurrent requests (2)"}, {}, None),
        (QUEUE_FULL, {"Retry-After": "soon"}, 30.0),
    ],
    ids=["header", "body", "neither", "bad-header"],
)
def test_a_rate_limited_submit_is_sent_once_with_the_wait(
    wire, provider, no_backoff, body, headers, wait
):
    """Higgsfield made nothing (docs: concepts/rate-limits); the runner waits
    `retry_after_s` and sends it again, so the transport does not."""
    fake = wire({SUBMIT: (429, body, headers)})

    with pytest.raises(ProviderRateLimitError) as raised:
        provider.submit(SPEC, {}, "job")

    assert raised.value.retry_after_s == wait
    assert len(fake.calls) == 1
    assert error_reason(str(raised.value)) == "Rate limited"


@pytest.mark.parametrize("status", [408, 502, 503, 504])
def test_an_unanswered_submit_is_resent_under_one_key_then_remembers_nothing(
    wire, provider, no_backoff, status
):
    """2026-10-25 review #61: the SDK resent a 5xx/408 submit with no key --
    up to 3 paid requests. With the key a resend returns the request a lost
    answer made (docs: concepts/idempotency)."""
    fake = wire({SUBMIT: (status, {"detail": "busy"})})

    with pytest.raises(ProviderUnavailableError):
        generate(provider, SPEC, {"prompt": "a"}, "job-1")

    keys = {c["headers"]["idempotency-key"] for c in fake.calls}
    assert len(fake.calls) == 3
    assert len(keys) == 1
    assert tasks.recall("job-1") is None


def test_network_trouble_is_unavailable(wire, provider):
    wire({("GET", "/requests/r1/status"): httpx.ConnectError("down")})

    with pytest.raises(ProviderUnavailableError, match="unreachable"):
        provider.status("r1")


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "face.png"
    Image.new("RGB", (4, 4)).save(path)
    return path


def _upload_routes(put=(200, {})):
    return {
        ("POST", "/files/generate-upload-url"): (
            200,
            {
                "public_url": "https://cdn/input/face.png",
                "upload_url": "https://storage.example/put?sig=1",
                "content_type": "image/png",
                "upload_headers": {"Content-Type": "image/png"},
            },
        ),
        ("PUT", "/put"): put,
    }


def test_upload_presigns_then_puts_the_bytes_without_the_key(wire, provider, image):
    fake = wire(_upload_routes())

    assert provider.upload(image) == "https://cdn/input/face.png"
    presign, put = fake.calls
    assert presign["json"] == {"content_type": "image/png"}
    assert put["url"].startswith("https://storage.example/put")
    assert put["content"] == image.read_bytes()
    assert "authorization" not in put["headers"], "never send the key to storage"


def test_a_refused_storage_put_is_no_key_or_balance_problem(wire, provider, image):
    """A presigned PUT's 403 is a stale signature: neither "wrong API key"
    nor "No credits" (either would stop a whole /clone-links batch)."""
    wire(_upload_routes(put=(403, {"detail": "Request has expired"})))

    with pytest.raises(ProviderInputError) as raised:
        provider.upload(image)

    assert type(raised.value) is ProviderInputError


@pytest.mark.parametrize("name", ["face.webp", "FACE.WEBP"])
def test_a_webp_subject_goes_up_as_webp(wire, provider, tmp_path, name):
    """The image's Python knows no .webp; a WebP subject was refused
    (dev 2026-10-25)."""
    path = tmp_path / name
    Image.new("RGB", (4, 4)).save(path, "WEBP")
    fake = wire(_upload_routes())

    assert provider.upload(path) == "https://cdn/input/face.png"
    presign, put = fake.calls
    assert presign["json"] == {"content_type": "image/webp"}
    assert put["content"] == path.read_bytes()


@pytest.mark.parametrize(("suffix", "fmt"), [(".tiff", "TIFF"), (".bmp", "BMP")])
def test_a_bmp_or_tiff_subject_goes_up_as_a_jpeg_copy(
    wire, provider, tmp_path, monkeypatch, suffix, fmt
):
    """2026-10-25 review #28: the asset store takes BMP/TIFF, Higgsfield does
    not -- every run of a batch failed after downloading its reel."""
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path / "out"))
    subject = tmp_path / f"abc__subject{suffix}"
    Image.new("RGB", (40, 30), "red").save(subject, fmt)
    original = subject.read_bytes()
    fake = wire(_upload_routes())

    provider.upload(subject)

    presign, put = fake.calls
    assert presign["json"] == {"content_type": "image/jpeg"}
    sent = tmp_path / "out" / "higgsfield" / "fitted" / "abc__subject.jpg"
    assert put["content"] == sent.read_bytes()
    with Image.open(sent) as img:
        assert (img.format, img.size) == ("JPEG", (40, 30))
    assert subject.read_bytes() == original


def test_a_reused_reference_is_uploaded_once(wire, provider, image):
    fake = wire(_upload_routes())
    head = mock.Mock(status_code=200)

    with mock.patch(
        "ofmhelpers.aigenproviders.higgsfield.provider.requests.head", return_value=head
    ):
        first = provider.upload(image)
        second = HiggsfieldProvider("key-id:key-secret").upload(image)

    assert first == second
    assert len(fake.calls) == 2, "presign + PUT once, then the cache"


def test_a_resumed_run_whose_subject_is_gone_still_gets_its_url(
    wire, provider, tmp_path, monkeypatch
):
    """The cache is looked up before the file is read: a run resumed after
    its subject left the store still builds its payload."""
    monkeypatch.setenv("OFM_HF_REF_IMAGE_MAX_SIDE", "500")
    monkeypatch.setattr(
        "ofmhelpers.aigenproviders.higgsfield.provider._live", lambda _url: True
    )
    sheet = tmp_path / "sheet.png"
    Image.new("RGB", (2000, 400)).save(sheet)
    fake = wire(_upload_routes())
    first = provider.upload(sheet)
    sheet.unlink()

    assert provider.upload(sheet) == first
    assert len(fake.calls) == 2


def test_another_account_never_reuses_the_upload(wire, provider, image):
    fake = wire(_upload_routes())

    with mock.patch(
        "ofmhelpers.aigenproviders.higgsfield.provider.requests.head",
        return_value=mock.Mock(status_code=200),
    ):
        provider.upload(image)
        HiggsfieldProvider("other:account").upload(image)

    assert len(fake.calls) == 4


def test_a_file_type_higgsfield_refuses_is_rejected_before_any_call(
    wire, provider, tmp_path
):
    fake = wire({})
    clip = tmp_path / "clip.mov"
    clip.write_bytes(b"x")

    with pytest.raises(ProviderInputError, match="MP4"):
        provider.upload(clip)
    assert fake.calls == []


def test_an_image_over_the_limits_goes_up_as_a_shrunk_copy(
    wire, provider, tmp_path, monkeypatch
):
    """An 8262 px character sheet failed Wan 3.0 after submit (2026-10-19)."""
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("OFM_HF_REF_IMAGE_MAX_SIDE", "500")
    sheet = tmp_path / "sheet.png"
    Image.new("RGB", (2000, 400)).save(sheet)
    original = sheet.read_bytes()
    fake = wire(_upload_routes())

    provider.upload(sheet)

    presign, put = fake.calls
    assert presign["json"] == {"content_type": "image/jpeg"}
    sent = tmp_path / "out" / "higgsfield" / "fitted" / "sheet.jpg"
    assert put["content"] == sent.read_bytes()
    with Image.open(sent) as img:
        assert img.size == (500, 100)
    assert sheet.read_bytes() == original


def _upload_at_once(monkeypatch, path, count=3):
    """`count` workers upload `path` together over a slow wire; returns their
    URLs, the wire, and whether the Redis lock was held during each call."""
    fake = Wire(_upload_routes())
    lock = f"lock:{HiggsfieldProvider('key-id:key-secret')._upload_cache_key(path)}"
    locked = []

    def slow(client, method, url, **kwargs):
        locked.append(bool(get_redis().exists(lock)))
        time.sleep(0.2)  # long enough for all of them to be in flight
        return fake(client, method, url, **kwargs)

    monkeypatch.setattr(httpx.Client, "request", slow)
    monkeypatch.setattr(
        "ofmhelpers.aigenproviders.higgsfield.provider._live", lambda _url: True
    )
    urls = []
    threads = [
        threading.Thread(
            target=lambda: urls.append(
                HiggsfieldProvider("key-id:key-secret").upload(path)
            )
        )
        for _ in range(count)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return urls, fake, locked


def test_one_file_asked_for_at_once_is_uploaded_once(monkeypatch, image):
    """2026-10-23: a /clone-long run's 3 pieces started together and each
    uploaded the same subject image (3 x ~15 s). Now the first uploads, the
    others wait and take its URL -- under the Redis lock, which every worker
    process shares (an in-process lock let two /clone-links runs upload one
    image twice, dev 2026-10-25)."""
    urls, fake, locked = _upload_at_once(monkeypatch, image)

    assert urls == ["https://cdn/input/face.png"] * 3
    assert len(fake.calls) == 2, "one presign + one PUT for all three"
    assert locked == [True, True]


def test_an_oversized_subject_asked_for_at_once_is_shrunk_and_sent_once(
    monkeypatch, tmp_path
):
    """2026-10-25 review #6: the shrunk copy was written outside the lock and
    in place, so a batch could upload (and cache for 24 h) a half-written
    JPEG."""
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("OFM_HF_REF_IMAGE_MAX_SIDE", "500")
    sheet = tmp_path / "sheet.png"
    Image.effect_noise((2000, 400), 64).convert("RGB").save(sheet)

    urls, fake, _locked = _upload_at_once(monkeypatch, sheet)

    assert urls == ["https://cdn/input/face.png"] * 3
    presign, put = fake.calls
    assert presign["json"] == {"content_type": "image/jpeg"}
    copy = tmp_path / "out" / "higgsfield" / "fitted" / "sheet.jpg"
    assert put["content"] == copy.read_bytes()
    with Image.open(copy) as img:
        img.load()
        assert img.size == (500, 100)
