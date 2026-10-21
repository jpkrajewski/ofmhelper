import datetime
import math
import random
from zoneinfo import ZoneInfo

from ofmhelpers.config import settings
from ofmhelpers.iphones.models import GeoPoint, PerRecording, Place, Recording

NYC = GeoPoint(latitude=40.7549, longitude=-73.9840, altitude_m=10.0)


def _metres(a: GeoPoint, b: GeoPoint) -> float:
    north = (a.latitude - b.latitude) * 111_320
    east = (a.longitude - b.longitude) * 111_320 * math.cos(math.radians(a.latitude))
    return math.hypot(north, east)


def test_iso6709_is_written_like_an_iphone():
    real = GeoPoint(latitude=-32.953, longitude=-60.6736, altitude_m=29.229)

    assert real.iso6709 == "-32.9530-060.6736+029.229/"
    assert NYC.iso6709 == "+40.7549-073.9840+010.000/"


def test_every_sample_lands_inside_the_jitter_radius_and_accuracy_range():
    place = Place(center=NYC, jitter_m=1500, accuracy_m=(2.0, 2.7))
    rng = random.Random(7)  # noqa: S311 - not cryptography

    samples = [place.sample(rng) for _ in range(500)]

    assert all(_metres(p, NYC) <= 1500.01 for p, _ in samples)
    assert all(2.0 <= acc <= 2.7 for _, acc in samples)
    # Spread out, not stuck at the centre.
    assert len({p.iso6709 for p, _ in samples}) > 450


def test_default_place_is_new_york_in_new_york_time():
    cfg = settings.exports

    assert cfg.device_place is not None
    assert cfg.device_place.center == NYC
    assert cfg.device_timezone == "America/New_York"


def test_creation_date_follows_new_york_daylight_saving():
    tz = ZoneInfo("America/New_York")
    summer = Recording(end=datetime.datetime(2026, 7, 1, 12, tzinfo=tz), duration_s=10)
    winter = Recording(end=datetime.datetime(2026, 1, 1, 12, tzinfo=tz), duration_s=10)

    assert summer.value(PerRecording.CREATION_DATE) == "2026-07-01T11:59:50-0400"
    assert winter.value(PerRecording.CREATION_DATE) == "2026-01-01T11:59:50-0500"


def test_no_place_means_no_location():
    recording = Recording.ending_now(ZoneInfo("America/New_York"), None)

    assert recording.value(PerRecording.LOCATION_ISO6709) is None
    assert recording.value(PerRecording.LOCATION_ACCURACY) is None
