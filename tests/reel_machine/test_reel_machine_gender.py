"""
gender.py's placeholder resolution -- the mapping used by shapes.render_shape
to turn "she"/"her"/"female" hardcoding into pronoun-correct text for
whichever gender the user picks.
"""

import pytest

from ofmhelpers.reel_machine.gender import (
    DEFAULT_GENDER,
    GENDERS,
    get_gender,
    render_text,
)


def test_default_gender_is_female():
    assert DEFAULT_GENDER == "female"
    assert get_gender(None) is GENDERS["female"]


def test_unknown_gender_falls_back_to_default():
    assert get_gender("something-invalid") is GENDERS[DEFAULT_GENDER]


@pytest.mark.parametrize("key", ["female", "male", "nonbinary"])
def test_render_text_fills_every_placeholder(key):
    gender = GENDERS[key]
    template = "{label}/{noun}/{noun_cap}/{subject}/{object}/{possessive}/{tag}"
    rendered = render_text(template, gender)
    assert "{" not in rendered
    assert gender.label in rendered
    assert gender.tag in rendered


def test_template_with_no_placeholders_passes_through_unchanged():
    gender = GENDERS["male"]
    assert (
        render_text("none -- ambient audio only", gender)
        == "none -- ambient audio only"
    )


def test_noun_cap_is_capitalized():
    assert GENDERS["male"].noun_cap == "Man"
    assert GENDERS["nonbinary"].noun_cap == "Person"
