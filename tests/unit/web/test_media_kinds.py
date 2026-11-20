import pytest

from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.integrations.downloaders.generic import VIDEO_EXTENSIONS


@pytest.mark.parametrize("ext", sorted(VIDEO_EXTENSIONS))
def test_every_video_a_download_keeps_is_a_video(ext):
    assert classify_kind(f"reel{ext}") == "video"
    assert classify_kind(f"REEL{ext.upper()}") == "video"
