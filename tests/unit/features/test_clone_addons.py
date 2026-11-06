"""/clone's prompt add-ons (features/clone/addons.json): each chip lights up
for the wordings the clone prompts on prod used (2026-11-06), never for a
plain prompt, and the Start dialog asks the four that same-video re-runs
most often had to add."""

import re

import pytest

from ofmhelpers.features.clone.addons import prompt_addons

# Wordings typed on prod and the add-on each one asks for.
ASKED = [
    ("he is wearing same clothes from @video1", "same_clothes"),
    ("he is wearing the same clothes as the guy in @video1", "same_clothes"),
    ("she is wearing clothes from  @video1", "same_clothes"),
    ("make him wear the same clothes as @video1", "same_clothes"),
    ("remove text overlay", "text_overlay"),
    ("remove textoverlay", "text_overlay"),
    ("remove overlay text", "text_overlay"),
    ("remove over lay text", "text_overlay"),
    ("VISIBLE FACE, NO WATERMARKS, NO TEXT OVERLAY", "text_overlay"),
    ("no tattoos", "tattoos"),
    ("NO TATTOS", "tattoos"),
    ("NO TATOOS, NO EARINGS", "tattoos"),
    ("she is wearing clothes from @image2", "clothes_image2"),
    ("she is wearing skirt and hoodie from @Image2", "clothes_image2"),
    ("replace the reflection in TV also", "reflections"),
    ("replace the subject in phone screen in @video1", "reflections"),
    ("remove piercing from nipple", "jewelry"),
    ("NO TATOOS, NO EARINGS", "jewelry"),
    ("remove gold chain from his neck", "jewelry"),
    ("SHOWING FACE", "face"),
]

# Prompts that ask for none of them ("wearing" is not "earing").
PLAIN = [
    "Replace the subject in @Video1 with subject from @Image1",
    "he is wearing cowboy hat",
    "replace the back ground with @image2",
    "Replace the GUY IN BLACK TANK TOP in @Video1 with subject from @Image1",
]


def _lit(text: str) -> set[str]:
    return {a.key for a in prompt_addons() if re.search(a.pattern, text, re.IGNORECASE)}


@pytest.mark.parametrize(("text", "key"), ASKED)
def test_a_prod_wording_lights_its_addon(text, key):
    assert key in _lit(text)


@pytest.mark.parametrize("text", PLAIN)
def test_a_plain_prompt_lights_none(text):
    assert _lit(text) == set()


@pytest.mark.parametrize("addon", prompt_addons(), ids=lambda a: a.key)
def test_what_a_click_adds_lights_its_own_chip(addon):
    assert _lit(addon.text) >= {addon.key}


def test_the_start_dialog_asks_what_reruns_added_most():
    assert [a.key for a in prompt_addons() if a.check] == [
        "same_clothes",
        "text_overlay",
        "tattoos",
        "jewelry",
    ]


def test_the_chips_come_in_prod_use_order_with_one_key_each():
    keys = [a.key for a in prompt_addons()]
    assert keys[:3] == ["same_clothes", "text_overlay", "tattoos"]
    assert len(keys) == len(set(keys))
