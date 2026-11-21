import pytest

from ofmhelpers.media.recorder import load_profile
from ofmhelpers.media.repurpose.probe import Source
from ofmhelpers.media.repurpose.repurpose import master_for


def _source(width: int, height: int, fps: float) -> Source:
    return Source(duration=10, width=width, height=height, has_audio=True, fps=fps)


@pytest.mark.parametrize(
    ("profile", "source", "max_width", "fps"),
    [
        ("iphone16", _source(2160, 3840, 60), 1620, 30),
        ("iphone16", _source(3840, 2160, 60), 2880, 30),
        ("iphone16", _source(1080, 1920, 30), None, "source"),
        ("iphone16", _source(720, 1280, 25), None, "source"),
        ("iphone16_4k", _source(2160, 3840, 60), None, 30),
        ("iphone16_4k", _source(2160, 3840, 30000 / 1001), None, "source"),
    ],
)
def test_the_master_is_no_bigger_or_faster_than_the_recorder_needs(
    profile, source, max_width, fps
):
    video = master_for(load_profile(profile), source).video

    assert (video.max_width, video.fps) == (max_width, fps)


def test_a_wide_source_keeps_enough_height_for_the_recorder_frame():
    profile = load_profile("iphone16")
    source = _source(4096, 2160, 30)

    video = master_for(profile, source).video

    assert video.max_width is not None
    height = source.height * video.max_width / source.width
    assert video.max_width >= profile.video.height
    assert height >= profile.video.width
