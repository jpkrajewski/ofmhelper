# Copyright (C) 2026 jpkrajewski

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
