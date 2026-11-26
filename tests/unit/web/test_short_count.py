# Copyright (C) 2026 jpkrajewski

import pytest

from ofmhelpers.web.templates_config import get_templates


@pytest.mark.parametrize(
    ("n", "shown"),
    [
        (999, "999"),
        (1000, "1k"),
        (1524, "1.5k"),
        (9949, "9.9k"),
        (9960, "10k"),
        (14_233, "14k"),
        (434_347, "434k"),
        (999_499, "999k"),
        (999_500, "1.0M"),
        (1_000_000, "1.0M"),
        (3_000_000, "3.0M"),
        (1_260_000, "1.3M"),
        (12_400_000, "12.4M"),
    ],
)
def test_a_count_is_shortened_without_a_wrong_unit(n, shown):
    template = get_templates().env.from_string(
        '{% import "_reel_counts.html" as rc %}{{ rc.short_count(n) }}',
    )
    assert template.render(n=n).strip() == shown
