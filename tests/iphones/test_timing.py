import random

from ofmhelpers.iphones import load_profile
from ofmhelpers.iphones.recorder import encode_args
from ofmhelpers.utils.capcut import SourceInfo

TIMING = load_profile("iphone17").video.timing


def test_first_frame_holds_for_four_slots_like_the_camera_start():
    plan = TIMING.plan(random.Random(1), 300)  # noqa: S311 - not cryptography

    assert plan.dropped[:3] == [1, 2, 3]


def test_plans_stay_inside_the_sampled_ranges():
    for seed in range(200):
        plan = TIMING.plan(random.Random(seed), 300)  # noqa: S311 - not cryptography

        startup = plan.dropped[3:]
        assert len(startup) <= TIMING.startup_gaps[1] * TIMING.gap_drops[1]
        assert all(i < 20 for i in startup)
        assert 1 <= len(plan.nudges) <= 2
        assert plan.nudges == sorted(plan.nudges)
        assert 12 <= plan.last_frame_ticks <= 21


def test_a_short_clip_never_drops_its_last_frame():
    plan = TIMING.plan(random.Random(3), 5)  # noqa: S311 - not cryptography

    assert all(i < 4 for i in plan.dropped)


def test_mirror_flips_before_anything_else():
    profile = load_profile("iphone17")
    src = SourceInfo(duration=2.0, width=720, height=1280, has_audio=True)
    plan = profile.video.timing.plan(random.Random(1), 60)  # noqa: S311 - not cryptography

    mirrored = encode_args(profile, src, plan, mirror=True)
    plain = encode_args(profile, src, plan)

    assert mirrored[mirrored.index("-vf") + 1].startswith("hflip,scale=")
    assert "hflip" not in plain[plain.index("-vf") + 1]
