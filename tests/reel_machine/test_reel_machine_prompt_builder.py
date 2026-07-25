"""
build_prompt_package must always emit the trimmed, ready-to-fire block
structure (SETUP/IMAGE REFERENCE MAP/PROMPT/VOICE & PACING/PER-SECOND
TIMELINE/EFFECTS/SCENE-LOCKS/NEGATIVE/VIRAL MECHANIC/CAMERA / LOOK) with the
settings-lock values (9:16, 480p draft -> 720p final, empty reference
audio/video) baked in -- these are the two things ported from the old
reel-machine bundle's package-spec.md / settings-lock.md that must never
silently drift. TARGET/PERSONA, RULE, SHAPE, PREFLIGHT, COST/RISK are
internal authoring scaffolding, deliberately left out of the VA-facing
output -- see prompt_builder.py's module docstring.
"""

from ofmhelpers.reel_machine.gender import GENDERS
from ofmhelpers.reel_machine.looks import LOOKS
from ofmhelpers.reel_machine.prompt_builder import SETTINGS, build_prompt_package
from ofmhelpers.reel_machine.shapes import SHAPES
from ofmhelpers.reel_machine.teardown import Beat, Teardown

REQUIRED_BLOCKS = [
    "SETUP",
    "IMAGE REFERENCE MAP",
    "PROMPT",
    "VOICE & PACING",
    "PER-SECOND TIMELINE",
    "EFFECTS",
    "SCENE-LOCKS",
    "NEGATIVE",
    "VIRAL MECHANIC",
    "CAMERA / LOOK",
]

REMOVED_BLOCKS = [
    "TARGET / PERSONA",
    "\nRULE\n",
    "\nSHAPE\n",
    "PREFLIGHT",
    "COST / RISK",
]


def _sample_teardown(**overrides) -> Teardown:
    defaults = dict(
        hook="hi there",
        beats=[Beat(start=0.0, end=1.5, text="hi there, watch this")],
        viral_mechanic="call-out hook -> withhold -> twist",
        camera_look="phone selfie, arm's length",
        duration=15.0,
    )
    defaults.update(overrides)
    return Teardown(**defaults)


def test_package_contains_every_required_block():
    package = build_prompt_package(
        _sample_teardown(), SHAPES["solo_monologue"], LOOKS["phone_selfie"], duration=15
    )
    for block in REQUIRED_BLOCKS:
        assert block in package, f"missing required block: {block}"


def test_package_omits_internal_authoring_blocks():
    package = build_prompt_package(
        _sample_teardown(),
        SHAPES["solo_monologue"],
        LOOKS["phone_selfie"],
        duration=15,
        target="confident fitness coach pitching a program",
    )
    for block in REMOVED_BLOCKS:
        assert block not in package, f"internal block leaked into output: {block!r}"


def test_settings_match_the_lock():
    package = build_prompt_package(
        _sample_teardown(), SHAPES["solo_monologue"], LOOKS["phone_selfie"], duration=15
    )
    assert SETTINGS["aspect_ratio"] in package
    assert SETTINGS["resolution_draft"] in package
    assert SETTINGS["resolution_final"] in package
    assert "EMPTY" in package  # reference audios/videos locked empty


def test_timeline_includes_every_beat():
    teardown = _sample_teardown()
    package = build_prompt_package(
        teardown, SHAPES["duet_pov"], LOOKS["gopro_pov"], duration=15
    )
    assert "hi there, watch this" in package


def test_every_shape_and_look_combination_builds_without_error():
    teardown = _sample_teardown()
    for shape in SHAPES.values():
        for look in LOOKS.values():
            package = build_prompt_package(teardown, shape, look, duration=15)
            assert look.negative in package


def test_gender_placeholders_are_fully_resolved_for_every_gender():
    """No shape template should leave a raw {placeholder} in the output --
    that would mean a shapes.py field uses a token render_text doesn't know
    about."""
    teardown = _sample_teardown()
    for gender_key in GENDERS:
        for shape in SHAPES.values():
            package = build_prompt_package(
                teardown, shape, LOOKS["phone_selfie"], duration=15, gender=gender_key
            )
            assert "{label}" not in package
            assert "{subject}" not in package
            assert "{object}" not in package
            assert "{possessive}" not in package
            assert "{noun}" not in package
            assert "{noun_cap}" not in package


def test_gender_changes_the_pronouns_and_speaker_tag():
    teardown = _sample_teardown()
    female_pkg = build_prompt_package(
        teardown,
        SHAPES["solo_monologue"],
        LOOKS["phone_selfie"],
        duration=15,
        gender="female",
    )
    male_pkg = build_prompt_package(
        teardown,
        SHAPES["solo_monologue"],
        LOOKS["phone_selfie"],
        duration=15,
        gender="male",
    )
    assert "SHE (on-cam" in female_pkg
    assert "HE (on-cam" in male_pkg


def test_default_pose_is_static_when_no_subject_action_detected():
    teardown = _sample_teardown()
    package = build_prompt_package(
        teardown, SHAPES["solo_monologue"], LOOKS["phone_selfie"], duration=15
    )
    assert (
        "ONE stable pose the entire clip -- no walk-in, no walking, no pose change."
        in package
    )


def test_subject_action_overrides_the_default_static_pose():
    """A vision provider's detected subject_action (e.g. "walks away from
    the camera the entire clip") must replace the hardcoded static-pose
    line -- otherwise a genuinely moving subject gets locked into a
    contradictory 'no walking' instruction (see reel_machine/CLAUDE.md)."""
    teardown = _sample_teardown(
        subject_action="walks away from the camera the entire clip, back turned, steady stride"
    )
    package = build_prompt_package(
        teardown, SHAPES["action_no_dialogue"], LOOKS["third_person"], duration=12
    )
    assert "walks away from the camera the entire clip" in package
    assert "no walk-in, no walking, no pose change" not in package
    # SCENE-LOCKS' [POSE] line reflects the same real motion, not the default.
    assert "[POSE] walks away from the camera the entire clip" in package
