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


def test_send_webhook_defaults_embeds_to_empty_list(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhooks/abc")
    fake_response = mock.Mock()
    fake_response.raise_for_status = mock.Mock()

    with mock.patch.object(
        discord_client.requests, "post", return_value=fake_response
    ) as post:
        discord_client.send_webhook("hello")
        assert post.call_args.kwargs["json"]["embeds"] == []


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
