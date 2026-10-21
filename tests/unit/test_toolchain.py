"""The test image has every binary the suite drives (Dockerfile `test`
stage). Missing one fails here, loudly, instead of tests skipping
themselves into a green run."""

import shutil

import pytest


@pytest.mark.parametrize("binary", ["ffmpeg", "ffprobe", "exiftool", "node", "psql"])
def test_the_test_image_has(binary):
    assert shutil.which(binary), f"{binary} missing: run tests via scripts/test.sh"
