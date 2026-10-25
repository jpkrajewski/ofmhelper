"""`classify_kind` agrees with what the downloaders keep."""

import pytest

from ofmhelpers.downloaders.generic import VIDEO_EXTENSIONS
from ofmhelpers.web.media_kinds import classify_kind


@pytest.mark.parametrize("ext", sorted(VIDEO_EXTENSIONS))
def test_every_video_a_download_keeps_is_a_video(ext):
    """An .m4v download was no video here, so /clone-links would call it "No
    video at this link" (2026-10-25 review #28)."""
    assert classify_kind(f"reel{ext}") == "video"
    assert classify_kind(f"REEL{ext.upper()}") == "video"
