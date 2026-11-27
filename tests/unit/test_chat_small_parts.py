# Copyright (C) 2026 jpkrajewski

import logging
from datetime import UTC, datetime

import pytest

from ofmhelpers.db.chats.schema import (
    ChatEvent,
    ChatPosition,
    ChatRead,
    ChatTokens,
    ChatWebhook,
    CreatedSubscription,
    ReadScope,
)
from ofmhelpers.features.notifications.service import money
from ofmhelpers.monitoring.log_records import RedactFilter
from ofmhelpers.redact import MASK


@pytest.mark.parametrize(
    ("cents", "currency", "shown"),
    [
        (5000, None, "$50"),
        (5050, "USD", "$50.50"),
        (100, "EUR", "EUR 1"),
        (None, None, "$0"),
    ],
)
def test_money_reads_like_money(cents, currency, shown):
    assert money(cents, currency) == shown


def _access_line(path):
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", path, "1.1", 303),
        None,
    )


@pytest.mark.parametrize(
    ("path", "shown"),
    [
        (
            "/chatting/fanvue/callback?code=SECRET&state=abc",
            f"code={MASK}&state={MASK}",
        ),
        ("/auth/google/callback?state=abc&code=SECRET", f"state={MASK}&code={MASK}"),
        ("/home", "/home"),
        ("/generate?promo=code", "promo=code"),
    ],
)
def test_an_access_line_never_holds_a_login_code(path, shown):
    record = _access_line(path)

    assert RedactFilter().filter(record) is True

    assert shown in record.getMessage()
    assert "SECRET" not in record.getMessage()


def test_an_ordinary_log_line_is_left_alone():
    record = logging.LogRecord("x", logging.INFO, "", 0, "plain %s", ("words",), None)

    RedactFilter().filter(record)

    assert record.getMessage() == "plain words"


def test_a_fans_name_is_made_storable():
    read = ChatRead(
        channel_id="c",
        model_username="m",
        fan_name="a\x00b" + "x" * 300,
        last_message_id="1",
        events=[ChatEvent(message_id="1", from_fan=True, at=1.0)],
    )

    assert read.fan_name.startswith("ab")
    assert len(read.fan_name) == 255


def test_a_round_knows_each_chats_last_message_read():
    scope = ReadScope.since(
        [
            ChatPosition(channel_id="a", last_message_id="7"),
            ChatPosition(channel_id="b", last_message_id="9"),
        ],
        datetime(2026, 1, 1, tzinfo=UTC),
        {"b"},
        thorough=False,
    )

    assert scope.seen == {"a": "7", "b": "9"}
    assert scope.open_channels == {"b"}
    assert scope.thorough is False


def test_a_created_subscription_secret_is_masked_but_recoverable():
    subscription = CreatedSubscription(
        id="sub",
        url="https://example.invalid",
        secret="created-secret",
    )

    assert "created-secret" not in repr(subscription)
    assert "created-secret" not in subscription.model_dump_json()
    assert subscription.secret.get_secret_value() == "created-secret"


def test_a_webhook_secret_is_masked_but_recoverable():
    webhook = ChatWebhook(
        model_id="model",
        model_username="creator",
        webhook_id="hook",
        url="https://example.invalid",
        secret="webhook-secret",
    )

    assert "webhook-secret" not in repr(webhook)
    assert "webhook-secret" not in webhook.model_dump_json()
    assert webhook.secret.get_secret_value() == "webhook-secret"


def test_chat_tokens_are_masked_but_recoverable():
    tokens = ChatTokens(
        access_token="access-secret",
        refresh_token="refresh-secret",
        expires_at=123.0,
    )

    representation = repr(tokens)
    serialized = tokens.model_dump_json()
    for secret in ("access-secret", "refresh-secret"):
        assert secret not in representation
        assert secret not in serialized
    assert tokens.access_token.get_secret_value() == "access-secret"
    assert tokens.refresh_token.get_secret_value() == "refresh-secret"
