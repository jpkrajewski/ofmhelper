"""The small parts of /chatting that need nothing around them."""

import logging

import pytest

from ofmhelpers.features.chatting.alerts import money
from ofmhelpers.log import _HideLoginCodes
from ofmhelpers.schemas.chatting import ChatEvent, ChatRead


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
        ("/chatting/fanvue/callback?code=SECRET&state=abc", "code=***&state=abc"),
        ("/auth/google/callback?state=abc&code=SECRET", "state=abc&code=***"),
        ("/home", "/home"),
        ("/generate?promo=code", "promo=code"),
    ],
)
def test_an_access_line_never_holds_a_login_code(path, shown):
    record = _access_line(path)

    assert _HideLoginCodes().filter(record) is True

    assert shown in record.getMessage()
    assert "SECRET" not in record.getMessage()


def test_an_ordinary_log_line_is_left_alone():
    record = logging.LogRecord("x", logging.INFO, "", 0, "plain %s", ("words",), None)

    _HideLoginCodes().filter(record)

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
