"""An event as the Telegram message a person reads, what an `Event` accepts, and
the bot token and job arguments never reaching a span or a log in clear."""

import logging

import pytest
from pydantic import ValidationError

from ofmhelpers.integrations.senders.telegram import message_text
from ofmhelpers.integrations.senders.telegram.models import MAX_TEXT
from ofmhelpers.log import _config, _HideJobArguments
from ofmhelpers.redact import MASK, redact
from ofmhelpers.schemas.events import MAX_TITLE, Event

_TOKEN = "7712345678:AAHk3v9-Qx_2mZpLw8rT4yUiO1aSdFgHjKl"
_URL = "https://ascend.test/accounts"


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_title_text_and_link_with_a_blank_line_between():
    event = Event(
        name="links.clicks",
        title="3,000 clicks",
        text="Bio link of @model",
        url="https://ascend.test/links",
    )

    assert message_text(event) == (
        "3,000 clicks\n\nBio link of @model\n\nhttps://ascend.test/links"
    )


def test_an_event_with_only_a_title_is_its_title():
    assert message_text(Event(name="accounts.views", title="5k views today")) == (
        "5k views today"
    )


def test_a_long_text_is_cut_and_the_title_and_link_are_kept():
    event = Event(name="long", title="Title", text="x" * 10_000, url=_URL)

    text = message_text(event)

    assert text.startswith("Title\n\nxxx")
    assert text.endswith(f"…\n\n{_URL}")
    assert len(text) < MAX_TEXT


def test_a_long_title_is_cut_and_the_link_is_kept():
    text = message_text(Event(name="long", title="T" * 5000, text="body", url=_URL))

    assert text == f"{'T' * (MAX_TITLE - 1)}…\n\nbody\n\n{_URL}"


def test_emoji_count_twice_and_the_message_still_fits():
    text = message_text(Event(name="fire", title="🔥", text="🔥" * 3000, url=_URL))

    assert _units(text) <= MAX_TEXT
    assert text.endswith(f"…\n\n{_URL}")


def test_a_site_path_without_the_site_address_is_left_out():
    """A bare `/links` would read as a bot command."""
    assert message_text(Event(name="n", title="t", url="/links")) == "t"


@pytest.mark.parametrize(
    "values",
    [
        {"name": "", "title": "t"},
        {"name": "n", "title": ""},
        {"name": "n", "title": "   "},
        {"name": "n", "title": "t", "link": "/x"},
        {"name": "n", "title": "t", "url": "javascript:alert(1)"},
        {"name": "n", "title": "t", "url": "links"},
    ],
    ids=["no name", "no title", "blank title", "misspelt field", "script", "no slash"],
)
def test_a_wrong_event_is_refused_where_it_is_made(values):
    with pytest.raises(ValidationError):
        Event.model_validate(values)


def test_long_words_from_outside_are_cut_not_refused():
    event = Event(name="n", title="t" * 1000, text="x" * 100_000)

    assert len(event.title) == MAX_TITLE
    assert event.title.endswith("…")
    assert len(event.text) < 100_000


def test_the_bot_token_in_a_bot_api_url_is_masked():
    url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"

    assert redact(url) == f"https://api.telegram.org/bot{MASK}/sendMessage"


@pytest.mark.parametrize(
    "text",
    [
        "at 12:30, 9:16 video, job 1730000000:done",
        "key links.clicks:20261106:6f1c2a9e-1b2c-4d3e-8f9a-0b1c2d3e4f5a",
    ],
)
def test_a_time_a_ratio_or_a_dated_id_is_not_taken_for_a_token(text):
    assert redact(text) == text


def test_rq_failure_lines_lose_the_jobs_arguments():
    """RQ logs a failed job with `extra={'arguments': ..., 'kwargs': ...}`: an API
    key, a chat id, a webhook URL."""
    record = logging.LogRecord("rq.worker", logging.ERROR, "", 0, "boom", None, None)
    record.__dict__.update(
        arguments=("telegram", "-1001234567", {"title": "t"}),
        kwargs={"api_key": "sk-secret"},
        func="ofmhelpers.features.events.dispatch.deliver",
    )

    assert _HideJobArguments().filter(record)

    hidden = {k: record.__dict__[k] for k in ("arguments", "kwargs", "func")}
    assert hidden == {
        "arguments": "[hidden]",
        "kwargs": "[hidden]",
        "func": "ofmhelpers.features.events.dispatch.deliver",
    }


def test_the_app_logging_puts_that_filter_on_rqs_worker_logger():
    loggers = _config("INFO", "text", access_log=False)["loggers"]

    assert loggers["rq.worker"]["filters"] == ["hide_job_arguments"]
