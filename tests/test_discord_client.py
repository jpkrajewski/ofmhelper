"""
Covers discord/client.py: the minimal webhook sender behind the Discord
approval notifications (see web/routers/todo.py's upload_asset).
"""

import unittest.mock as mock

import pytest
import requests

from ofmhelpers.discord import client as discord_client


def test_send_webhook_posts_expected_payload(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhooks/abc")
    fake_response = mock.Mock()
    fake_response.raise_for_status = mock.Mock()

    with mock.patch.object(
        discord_client.requests, "post", return_value=fake_response
    ) as post:
        discord_client.send_webhook("hello", [{"title": "t"}])

    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://discord.example/webhooks/abc"
    assert kwargs["json"] == {"content": "hello", "embeds": [{"title": "t"}]}
    assert kwargs["timeout"] == 10
    fake_response.raise_for_status.assert_called_once()


def test_send_webhook_omits_embeds_key_entirely_when_none_given(monkeypatch):
    """Not just an empty list -- the "embeds" key must be absent altogether,
    matching the exact payload shape of a plain human-typed message with no
    embeds. A webhook message that includes an embeds array (even one
    unrelated to a URL also in content) unreliably fails to also get
    Discord's own auto-unfurl for that URL -- confirmed by testing -- so
    callers that want a bare link to unfurl reliably (see
    web/routers/todo.py's _notify_discord_for_review) must not attach any
    embeds to that call at all."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhooks/abc")
    fake_response = mock.Mock()
    fake_response.raise_for_status = mock.Mock()

    with mock.patch.object(
        discord_client.requests, "post", return_value=fake_response
    ) as post:
        discord_client.send_webhook("hello")
        assert post.call_args.kwargs["json"] == {"content": "hello"}


def test_send_webhook_omits_embeds_key_for_an_empty_list_too(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhooks/abc")
    fake_response = mock.Mock()
    fake_response.raise_for_status = mock.Mock()

    with mock.patch.object(
        discord_client.requests, "post", return_value=fake_response
    ) as post:
        discord_client.send_webhook("hello", [])
        assert "embeds" not in post.call_args.kwargs["json"]


def test_send_webhook_missing_env_var_raises(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with pytest.raises(KeyError):
        discord_client.send_webhook("hello")


def test_send_webhook_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhooks/abc")
    fake_response = mock.Mock()
    fake_response.raise_for_status = mock.Mock(
        side_effect=requests.HTTPError("400 Bad Request")
    )

    with mock.patch.object(discord_client.requests, "post", return_value=fake_response):
        with pytest.raises(requests.HTTPError):
            discord_client.send_webhook("hello")


def test_send_webhook_raises_on_network_error(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhooks/abc")
    with mock.patch.object(
        discord_client.requests,
        "post",
        side_effect=requests.ConnectionError("no route to host"),
    ):
        with pytest.raises(requests.ConnectionError):
            discord_client.send_webhook("hello")
