# Copyright (C) 2026 jpkrajewski

import logging

import pytest

from ofmhelpers.log import _HideLoginCodes
from ofmhelpers.monitoring.telemetry import _masked_attributes
from ofmhelpers.redact import MASK, redact

TOKEN = "s3cretT0kenValue"


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        (f"/password/{TOKEN}", f"/password/{MASK}"),
        (f"/password/{TOKEN}?next=/home", f"/password/{MASK}?next=/home"),
        (f"/files/ai/{TOKEN}/clip.mp4", f"/files/ai/{MASK}/clip.mp4"),
        (
            f"GET http://x/files/ai/{TOKEN}/a.png 200",
            f"GET http://x/files/ai/{MASK}/a.png 200",
        ),
    ],
)
def test_redact_masks_path_tokens(raw, masked):
    assert redact(raw) == masked


def test_redact_leaves_other_paths_alone():
    assert redact("/files/ai") == "/files/ai"
    assert redact("/home/password-help") == "/home/password-help"


def test_span_attributes_carry_no_path_token():
    attributes = {
        "http.target": f"/password/{TOKEN}",
        "http.url": f"https://x.test/files/ai/{TOKEN}/v.mp4",
        "logfire.msg": f"GET /password/{TOKEN}",
    }
    out = _masked_attributes(attributes).model_dump()
    assert TOKEN not in "".join(out.values())


def test_access_log_hides_path_tokens():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "%s %s %s %s %s",
        ("1.2.3.4", "GET", f"/password/{TOKEN}", "1.1", 200),
        None,
    )
    _HideLoginCodes().filter(record)
    assert TOKEN not in record.getMessage()
    assert MASK in record.getMessage()
