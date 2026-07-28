"""Covers runpod/client.py. HTTP is mocked -- no server is contacted."""

from unittest import mock

import pytest

from ofmhelpers.runpod import client as client_module
from ofmhelpers.runpod.client import ComfyUIClient


def _client(**kwargs):
    return ComfyUIClient("http://comfy.test", poll_interval_s=0, **kwargs)


def _response(status_code=200, payload=None):
    r = mock.Mock()
    r.status_code = status_code
    r.json.return_value = payload if payload is not None else {}
    return r


def test_submit_returns_prompt_id():
    with mock.patch.object(
        client_module.requests,
        "post",
        return_value=_response(payload={"prompt_id": "p1"}),
    ) as post:
        assert _client().submit({"1": {}}) == "p1"

    body = post.call_args.kwargs["json"]
    assert body["prompt"] == {"1": {}}
    assert body["client_id"]


def test_submit_surfaces_node_errors_from_a_400():
    """/prompt returns 400 with the useful part in the body; raise_for_status
    alone would throw that away."""
    payload = {
        "error": {"type": "prompt_outputs_failed_validation", "message": "bad"},
        "node_errors": {
            "5": {
                "class_type": "CLIPLoader",
                "errors": [
                    {"message": "Value not in list", "details": "type: 'krea2' not in"}
                ],
            }
        },
    }
    with (
        mock.patch.object(
            client_module.requests, "post", return_value=_response(400, payload)
        ),
        pytest.raises(RuntimeError) as excinfo,
    ):
        _client().submit({"5": {}})

    message = str(excinfo.value)
    assert "node 5" in message
    assert "CLIPLoader" in message
    assert "krea2" in message


def test_wait_returns_the_history_entry_once_completed():
    entry = {"status": {"completed": True, "status_str": "success"}, "outputs": {}}
    with mock.patch.object(
        client_module.requests, "get", return_value=_response(payload={"p1": entry})
    ):
        assert _client().wait("p1") == entry


def test_wait_raises_on_an_execution_error():
    entry = {
        "status": {
            "completed": True,
            "status_str": "error",
            "messages": [
                [
                    "execution_error",
                    {
                        "node_id": "8",
                        "node_type": "KSampler",
                        "exception_type": "RuntimeError",
                        "exception_message": "out of memory",
                    },
                ]
            ],
        }
    }
    with (
        mock.patch.object(
            client_module.requests, "get", return_value=_response(payload={"p1": entry})
        ),
        pytest.raises(RuntimeError, match="out of memory"),
    ):
        _client().wait("p1")


def test_wait_times_out_when_the_prompt_never_completes():
    with (
        mock.patch.object(
            client_module.requests, "get", return_value=_response(payload={})
        ),
        pytest.raises(TimeoutError),
    ):
        _client().wait("p1", timeout_s=0.01)


def test_outputs_prefers_saved_images_over_previews():
    history = {
        "outputs": {
            "9": {"images": [{"filename": "preview.png", "type": "temp"}]},
            "29": {"images": [{"filename": "saved.png", "type": "output"}]},
        }
    }

    assert [i["filename"] for i in ComfyUIClient.outputs(history)] == ["saved.png"]


def test_outputs_falls_back_to_previews_when_nothing_was_saved():
    """Several of these workflows end in a PreviewImage; returning nothing
    for them would be wrong."""
    history = {"outputs": {"9": {"images": [{"filename": "p.png", "type": "temp"}]}}}

    assert [i["filename"] for i in ComfyUIClient.outputs(history)] == ["p.png"]


def test_download_writes_the_file(tmp_path):
    r = _response()
    r.content = b"bytes"
    with mock.patch.object(client_module.requests, "get", return_value=r):
        written = _client().download({"filename": "a.png"}, tmp_path)

    assert written.read_bytes() == b"bytes"


def test_upload_image_rejects_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        _client().upload_image(tmp_path / "nope.png")
