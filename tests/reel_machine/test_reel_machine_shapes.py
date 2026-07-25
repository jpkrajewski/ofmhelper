"""
render_shape must resolve every gender placeholder in every shape, for
every gender -- an unresolved {placeholder} leaking into a generated
Seedance package would be a real (if quiet) regression.
"""

import pytest

from ofmhelpers.reel_machine.gender import GENDERS
from ofmhelpers.reel_machine.shapes import SHAPES, render_shape

PLACEHOLDER_TOKENS = (
    "{label}",
    "{noun}",
    "{noun_cap}",
    "{subject}",
    "{object}",
    "{possessive}",
)


@pytest.mark.parametrize("shape_key", list(SHAPES))
@pytest.mark.parametrize("gender_key", list(GENDERS))
def test_render_shape_leaves_no_unresolved_placeholders(shape_key, gender_key):
    rendered = render_shape(SHAPES[shape_key], GENDERS[gender_key])
    for field in (
        rendered.name,
        rendered.voices,
        rendered.camera,
        rendered.pacing,
        rendered.notes,
    ):
        for token in PLACEHOLDER_TOKENS:
            assert (
                token not in field
            ), f"{shape_key}/{gender_key}: unresolved {token} in {field!r}"


def test_woman_x_woman_name_adapts_to_gender():
    female = render_shape(SHAPES["woman_x_woman"], GENDERS["female"])
    male = render_shape(SHAPES["woman_x_woman"], GENDERS["male"])
    assert female.name == "Woman x woman"
    assert male.name == "Man x man"


def test_solo_monologue_camera_uses_correct_pronoun():
    female = render_shape(SHAPES["solo_monologue"], GENDERS["female"])
    male = render_shape(SHAPES["solo_monologue"], GENDERS["male"])
    assert "she holds the phone" in female.camera
    assert "he holds the phone" in male.camera
