"""Higgsfield behind the shared interface, on the real SDK with its HTTP faked
at the wire (`httpx.Client.request`, which every SDK call goes through): the
auth header, the submit, the six statuses, the two-step upload and how
failures map to error types."""

from unittest import mock

import httpx
import pytest
from PIL import Image

from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderInputError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.higgsfield.provider import HiggsfieldProvider

BASE = "https://api.higgsfield.ai"
SPEC = get_spec("higgsfield:alibaba/wan-3.0/reference-to-video")


class Wire:
    """Answers each (method, path) from a table; records every call with the
    headers the client sent."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, client, method, url, **kwargs):
        full = (
            httpx.URL(str(client.base_url).rstrip("/") + "/" + str(url).lstrip("/"))
            if not str(url).startswith("http")
            else httpx.URL(url)
        )
        self.calls.append(
            {
                "method": method,
                "url": str(full),
                "headers": dict(client.headers),
                **kwargs,
            }
        )
        answer = self.routes[(method, full.path)]
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer()
        status, body = answer
        return httpx.Response(status, json=body, request=httpx.Request(method, full))


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


def test_submit_posts_the_payload_to_the_model_with_the_key(wire, provider):
    fake = wire(
        {
            ("POST", f"/{SPEC.model_id}"): (
                200,
                {
                    "status": "queued",
                    "request_id": "r1",
                    "status_url": f"{BASE}/requests/r1/status",
                    "cancel_url": f"{BASE}/requests/r1/cancel",
                },
            )
        }
    )

    assert provider.submit(SPEC, {"prompt": "p"}, idempotency_key="job") == "r1"
    [call] = fake.calls
    assert call["url"] == f"{BASE}/{SPEC.model_id}"
    assert call["json"] == {"prompt": "p"}
    assert call["headers"]["authorization"] == "Key key-id:key-secret"


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
    ("status", "error"),
    [(401, ProviderAuthError), (403, ProviderAuthError), (422, ProviderInputError)],
)
def test_http_errors_map_to_error_types(wire, provider, status, error):
    wire({("POST", f"/{SPEC.model_id}"): (status, {"detail": "nope"})})

    with pytest.raises(error, match="nope"):
        provider.submit(SPEC, {}, "job")


def test_a_5xx_outliving_the_sdks_retries_is_unavailable(wire, provider, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)
    fake = wire({("POST", f"/{SPEC.model_id}"): (503, {"detail": "busy"})})

    with pytest.raises(ProviderUnavailableError):
        provider.submit(SPEC, {}, "job")
    assert len(fake.calls) == 3, "the SDK's own retries ran first"


def test_network_trouble_is_unavailable(wire, provider):
    wire({("GET", "/requests/r1/status"): httpx.ConnectError("down")})

    with pytest.raises(ProviderUnavailableError, match="unreachable"):
        provider.status("r1")


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "face.png"
    Image.new("RGB", (4, 4)).save(path)
    return path


def _upload_routes():
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
        ("PUT", "/put"): (200, {}),
    }


def test_upload_presigns_then_puts_the_bytes_without_the_key(wire, provider, image):
    fake = wire(_upload_routes())

    assert provider.upload(image) == "https://cdn/input/face.png"
    presign, put = fake.calls
    assert presign["json"] == {"content_type": "image/png"}
    assert put["url"].startswith("https://storage.example/put")
    assert put["content"] == image.read_bytes()
    assert "authorization" not in put["headers"], "never send the key to storage"


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
