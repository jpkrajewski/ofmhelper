"""web/clone_long/models.py: how a long video is split and priced."""

import itertools

import pytest

from ofmhelpers.web.clone_long import Plan, Quote, Rate, Shape


def _lengths(seconds: float) -> list[float]:
    return [round(p.length, 3) for p in Plan.split(seconds, 15).pieces]


@pytest.mark.parametrize(
    ("seconds", "lengths"),
    [
        (3.0, [3.0]),
        (15.0, [15.0]),  # exactly the limit: one piece
        (15.2, [8.0, 7.2]),  # a 0.2 s tail would stutter: evened out
        (20.0, [14.0, 6.0]),  # long first piece, the rest is the tail
        (32.0, [11.0, 11.0, 10.0]),  # 14 + 14 + 4 would stutter: evened out
        (35.0, [14.0, 14.0, 7.0]),  # long pieces, a decent tail
        (45.0, [15.0, 15.0, 15.0]),
        (50.0, [14.0, 14.0, 14.0, 8.0]),  # as long as the cap allows
        (44.6, [14.8, 14.8, 15.0]),  # must exceed 14 to fit four... three pieces
        (71.0, [14.0, 14.0, 14.0, 14.0, 15.0]),
        (600.0, [15.0] * 40),
    ],
)
def test_a_video_splits_into_the_longest_pieces_without_a_sliver(seconds, lengths):
    assert _lengths(seconds) == lengths


@pytest.mark.parametrize("seconds", [15.01, 16, 29.9, 31, 47.3, 61, 89.5, 299.99])
def test_the_pieces_cover_the_video_once_none_over_the_limit(seconds):
    plan = Plan.split(seconds, 15)

    assert plan.pieces[0].start == 0
    for before, after in itertools.pairwise(plan.pieces):
        assert after.start == pytest.approx(before.start + before.length)
    assert sum(p.length for p in plan.pieces) == pytest.approx(seconds)
    assert all(0 < p.length <= 15 for p in plan.pieces)
    assert plan.count == -(-seconds // 15)
    # No stuttering sliver: every piece is at least a third of a full one.
    assert min(p.length for p in plan.pieces) >= 15 / 3 - 1e-6


def test_a_price_is_for_the_whole_length_at_the_resolutions_rate():
    rate = Rate(credits_per_s={"720P": 32.78, "1080P": 64.0}, usd_per_credit=0.005)

    quote = rate.quote(71.0, "720P", 5)

    assert quote == Quote(seconds=71.0, pieces=5, credits=2327.4, usd=11.64)
    assert quote.label == "71 s · 5 pieces · $11.64"
    assert rate.quote(71.0, "1080P", 5).usd == 22.72


def test_an_unpriced_resolution_costs_nothing_rather_than_guessing():
    assert Rate(credits_per_s={}, usd_per_credit=0.005).quote(10, "720P", 1).usd == 0


def test_one_piece_says_piece():
    assert (
        Quote(seconds=9, pieces=1, credits=1, usd=0.05).label == "9 s · 1 piece · $0.05"
    )


WAN3 = ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]


@pytest.mark.parametrize(
    ("size", "ratio"),
    [
        ((1920, 1080), "16:9"),
        ((1280, 720), "16:9"),
        ((1080, 1920), "9:16"),
        ((1080, 1080), "1:1"),
        ((1080, 1350), "3:4"),  # 4:5 is not offered: nearest
        ((2560, 1080), "21:9"),
        ((1440, 1080), "4:3"),
    ],
)
def test_a_shape_asks_for_the_nearest_offered_ratio(size, ratio):
    assert Shape(width=size[0], height=size[1]).nearest(WAN3) == ratio


@pytest.mark.parametrize(
    ("size", "height", "expected"),
    [
        ((1920, 1080), 720, (1280, 720)),
        ((1080, 1920), 1280, (720, 1280)),
        ((1080, 1350), 1024, (818, 1024)),  # 4:5 exactly, even sides
        ((1280, 720), 721, (1280, 720)),
    ],
)
def test_a_shape_at_a_height_keeps_its_aspect_with_even_sides(size, height, expected):
    assert Shape(width=size[0], height=size[1]).size_at(height) == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((3840, 2160), (1920, 1080)),  # 4K phone video, landscape
        ((2160, 3840), (1080, 1920)),  # and upright
        ((1080, 1350), None),  # short side already 1080
        ((1280, 720), None),
        ((1440, 1800), (1080, 1350)),
        ((4000, 4000), (1080, 1080)),
    ],
)
def test_a_shape_within_a_side_caps_its_short_side_only(size, expected):
    assert Shape(width=size[0], height=size[1]).within(1080) == expected
