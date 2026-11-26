# Copyright (C) 2026 jpkrajewski

from ofmhelpers.features.clone.addons import prompt_addons


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
