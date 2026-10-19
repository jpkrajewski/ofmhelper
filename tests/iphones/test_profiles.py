import pytest

from ofmhelpers.iphones import available, load_profile
from ofmhelpers.iphones.models import DataType, PerRecording


def test_iphone17_is_available():
    assert "iphone17" in available()


def test_iphone17_profile_is_fully_decoded():
    profile = load_profile("iphone17")

    assert profile.device.model == "iPhone 17 Pro Max"
    assert profile.video.codec_tag == "hvc1"
    assert (profile.video.width, profile.video.height) == (1080, 1920)
    assert profile.handlers.video == "Core Media Video"
    assert [k.key for k in profile.movie_keys][-1] == (
        "com.apple.quicktime.creationdate"
    )


def test_every_key_has_exactly_one_source_of_value():
    profile = load_profile("iphone17")

    for key in [*profile.movie_keys, *profile.video_track_keys]:
        assert (key.value is None) != (key.per_recording is None), key.key
        if key.data_type is DataType.SIGNED_INT_BE:
            assert isinstance(key.value, int)
            assert key.int_width is not None


def test_per_recording_values_are_not_baked_into_the_profile():
    keys = {k.key: k for k in load_profile("iphone17").movie_keys}

    assert keys["com.apple.quicktime.creationdate"].per_recording is (
        PerRecording.CREATION_DATE
    )
    assert keys["com.apple.quicktime.location.ISO6709"].value is None


def test_unknown_profile_is_an_error():
    with pytest.raises(ValueError, match="no iPhone profile"):
        load_profile("nokia3310")
