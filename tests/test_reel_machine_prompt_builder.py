"""
build_prompt_package must always emit the full package-spec block structure
(SETUP/RULE/IMAGE REFERENCE MAP/PROMPT/VOICE & PACING/PER-SECOND TIMELINE/
EFFECTS/SCENE-LOCKS/NEGATIVE/PREFLIGHT/COST) with the settings-lock values
(9:16, 480p draft -> 720p final, empty reference audio/video) baked in --
these are the two things ported from the old reel-machine bundle's
package-spec.md / settings-lock.md that must never silently drift.
"""

from ofmhelpers.reel_machine.gender import DEFAULT_GENDER, GENDERS, get_gender
from ofmhelpers.reel_machine.looks import LOOKS
from ofmhelpers.reel_machine.prompt_builder import SETTINGS, build_prompt_package
from ofmhelpers.reel_machine.shapes import SHAPES, render_shape
from ofmhelpers.reel_machine.teardown import Beat, Teardown

REQUIRED_BLOCKS = [
    "SETUP",
    "RULE",
    "IMAGE REFERENCE MAP",
    "PROMPT",
    "VOICE & PACING",
    "PER-SECOND TIMELINE",
    "EFFECTS",
    "SCENE-LOCKS",
    "NEGATIVE",
    "PREFLIGHT",
    "COST / RISK",
]


def _sample_teardown() -> Teardown:
    return Teardown(
        hook="hi there",
        beats=[Beat(start=0.0, end=1.5, text="hi there, watch this")],
        viral_mechanic="call-out hook -> withhold -> twist",
        camera_look="phone selfie, arm's length",
        duration=15.0,
    )


def test_package_contains_every_required_block():
    package = build_prompt_package(
        _sample_teardown(), SHAPES["solo_monologue"], LOOKS["phone_selfie"], duration=15
    )
    for block in REQUIRED_BLOCKS:
        assert block in package, f"missing required block: {block}"


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
    default_gender = get_gender(DEFAULT_GENDER)
    for shape in SHAPES.values():
        for look in LOOKS.values():
            package = build_prompt_package(teardown, shape, look, duration=15)
            assert render_shape(shape, default_gender).camera in package
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
    assert "she holds the phone" in female_pkg
    assert "SHE (on-cam" in female_pkg
    assert "he holds the phone" in male_pkg
    assert "HE (on-cam" in male_pkg


def test_target_brief_appears_in_the_package():
    teardown = _sample_teardown()
    package = build_prompt_package(
        teardown,
        SHAPES["solo_monologue"],
        LOOKS["phone_selfie"],
        duration=15,
        target="confident fitness coach pitching a program",
    )
    assert "confident fitness coach pitching a program" in package
    assert "TARGET / PERSONA" in package


def test_missing_target_leaves_an_edit_me_placeholder():
    teardown = _sample_teardown()
    package = build_prompt_package(
        teardown, SHAPES["solo_monologue"], LOOKS["phone_selfie"], duration=15
    )
    assert "(edit me)" in package.split("TARGET / PERSONA")[1].split("RULE")[0]
