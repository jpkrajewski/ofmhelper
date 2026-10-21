"""
Upload filenames are attacker-controlled: the multipart part's `filename` is
whatever the client typed, so "../../cookies/cookies.txt" is a legal value
and joining it onto an upload directory writes outside that directory. Every
path built from an upload name goes through task_helpers.safe_filename.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")


import pytest
from fastapi import HTTPException

from ofmhelpers.web.routers.task_helpers import safe_filename


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ("clip.mp4", "clip.mp4"),
        ("../../cookies/cookies.txt", "cookies.txt"),
        ("../../../etc/passwd", "passwd"),
        ("/absolute/path/clip.mp4", "clip.mp4"),
        (r"..\..\windows\style.txt", "style.txt"),
        ("weird name (1).png", "weird name (1).png"),
    ],
)
def test_safe_filename_keeps_only_the_basename(sent, expected):
    assert safe_filename(sent) == expected


@pytest.mark.parametrize("sent", ["", None, "..", ".", "../", "/"])
def test_safe_filename_rejects_names_with_no_basename(sent):
    with pytest.raises(HTTPException) as exc:
        safe_filename(sent)
    assert exc.value.status_code == 400
