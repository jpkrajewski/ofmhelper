"""Each builder against the bytes of the real iPhone 17 Pro Max recordings
the profile was decoded from (identical in all 7)."""

import datetime

from ofmhelpers.iphones import atoms, load_profile
from ofmhelpers.iphones.models import GeoPoint, Recording

PROFILE = load_profile("iphone17")

# Atom bodies (after size + type) as found in IMG_0042..0063.
VIDEO_HDLR = (
    "000000006d686c72766964656170706c000000000000000010436f7265204d6564696120566964656f"
)
AUDIO_HDLR = (
    "000000006d686c72736f756e6170706c000000000000000010436f7265204d6564696120417564696f"
)
DATA_HDLR = (
    "0000000064686c72616c69736170706c000000000000000017"
    "436f7265204d6564696120446174612048616e646c6572"
)
DINF = "0000001c6472656600000000000000010000000c616c697300000001"
TAPT = (
    "00000014636c656600000000043800000780000000000014"
    "70726f6600000000043800000780000000000014656e6f66"
    "000000000438000007800000"
)
META_HDLR = "00000000000000006d6474610000000000000000000000000000"


def _body(atom) -> str:
    return atom.to_bytes()[8:].hex()


def _recording(**kw) -> Recording:
    end = datetime.datetime(
        2026, 9, 15, 14, 43, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=-3))
    )
    return Recording(end=end, duration_s=11.0, **kw)


def test_handlers_match_the_recordings():
    assert _body(atoms.media_handler(PROFILE, "vide", "Core Media Video")) == (
        VIDEO_HDLR
    )
    assert _body(atoms.media_handler(PROFILE, "soun", "Core Media Audio")) == (
        AUDIO_HDLR
    )
    assert _body(atoms.data_handler(PROFILE)) == DATA_HDLR


def test_data_reference_and_aperture_match_the_recordings():
    assert _body(atoms.data_information(PROFILE)) == DINF
    assert _body(atoms.track_aperture(1080, 1920)) == TAPT


def test_video_track_meta_matches_the_recordings():
    meta = atoms.meta(PROFILE.video_track_keys, _recording())

    raw = meta.to_bytes()
    assert raw[8:34].hex() == "00000022" + "68646c72" + META_HDLR[:36]
    assert b"iPhone 17 Pro Max front camera 2.715mm f/1.9" in raw
    # Locale US/eng on the lens keys, as the recordings carry it.
    assert bytes.fromhex("00000001555315c7") + b"F1.90" in raw
    # Maker notes as 4-byte signed ints (type 21).
    assert bytes.fromhex("0000001500000000") + (6).to_bytes(4, "big") in raw


def test_movie_meta_dates_the_recording_start_in_its_offset():
    meta = atoms.meta(PROFILE.movie_keys, _recording())

    raw = meta.to_bytes()
    assert b"2026-09-15T14:43:19-0300" in raw
    assert bytes.fromhex("0000001500000000") + (1).to_bytes(8, "big") in raw


def test_location_keys_only_when_a_location_is_given():
    without = atoms.meta(PROFILE.movie_keys, _recording()).to_bytes()
    with_it = atoms.meta(
        PROFILE.movie_keys,
        _recording(
            location=GeoPoint(latitude=-32.953, longitude=-60.6736, altitude_m=29.229),
            location_accuracy_m=2.5,
        ),
    ).to_bytes()

    assert b"location" not in without
    assert b"-32.9530-060.6736+029.229/" in with_it
    assert b"2.500000" in with_it


def test_language_packing():
    assert atoms.pack_language("und") == 0x55C4
    assert atoms.pack_language("eng") == 0x15C7
