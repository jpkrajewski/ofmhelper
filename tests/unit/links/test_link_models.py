"""links/: the provider and target configs, slugs made from handles, the
Bouncy analytics spans, and the numbers /links derives from stored days."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from ofmhelpers.links import Usage, detect_target, provider_infos, targets
from ofmhelpers.links.providers import get_link_provider, link_providers
from ofmhelpers.links.providers.bouncy import _spans
from ofmhelpers.web.schemas import GenerateLinks, LinkPeriod, LinkSummary, ShortLink

OOPSIE = provider_infos()["oopsie"].slug
BOUNCY = provider_infos()["bouncy"].slug


def test_every_registered_provider_has_its_config_and_back():
    assert list(link_providers()) == list(provider_infos()) == ["oopsie", "bouncy"]
    for key, info in provider_infos().items():
        assert get_link_provider(key).info() is info
        assert info.default_domain == info.domains[0]
        assert info.key == key


def test_targets_are_onlyfans_fanvue_stacked():
    assert {k: t.label for k, t in targets().items()} == {
        "onlyfans": "OnlyFans",
        "fanvue": "Fanvue",
        "stacked": "Stacked",
    }


@pytest.mark.parametrize(
    ("url", "target"),
    [
        ("https://onlyfans.com/rachel.rosee/c124", "onlyfans"),
        ("https://www.fanvue.com/bighorsevip?free_trial=x", "fanvue"),
        ("https://www.stacked.com/@dane/membership", "stacked"),
        ("https://fanvue.com.evil.example/x", None),
        ("https://notonlyfans.com/x", None),
    ],
)
def test_detect_target_by_host(url, target):
    assert detect_target(url) == target


@pytest.mark.parametrize(
    ("handle", "oopsie", "bouncy"),
    [
        ("dany.walking", "dany.walking", "dany-walking"),
        ("_rachel_", "rachel", "rachel"),
        ("Jo", "jo", "jo-link"),
        ("a" * 60, "a" * 40, "a" * 50),
        ("x.__y", "x.__y", "x-__y"),
    ],
)
def test_slug_from_handle_follows_each_providers_rules(handle, oopsie, bouncy):
    assert OOPSIE.make(handle) == oopsie
    assert BOUNCY.make(handle) == bouncy


def test_numbered_slug_stays_within_max_length():
    assert BOUNCY.numbered("dane", 2) == "dane-2"
    long = "a" * 50
    assert BOUNCY.numbered(long, 12) == "a" * 47 + "-12"
    assert len(OOPSIE.numbered("b" * 40, 3)) == 40


def test_bouncy_spans_cover_the_range_in_31_day_pieces():
    start, end = date(2026, 7, 25), date(2026, 10, 22)
    spans = _spans(start, end, 31)
    assert spans[0][0] == start
    assert spans[-1][1] == end
    assert all((b - a).days < 31 for a, b in spans)
    assert all(
        spans[i][1] + timedelta(1) == spans[i + 1][0] for i in range(len(spans) - 1)
    )
    assert _spans(end, end, 31) == [(end, end)]


def test_room_is_the_tighter_of_total_and_active_limits():
    assert Usage(used=4, limit=10, active=3, active_limit=5).room == 2
    assert Usage(used=9, limit=10, active=3, active_limit=5).room == 1
    assert Usage(used=4, limit=10, active=6, active_limit=5).room == 0
    assert Usage(used=50, limit=50).room == 0
    assert Usage(used=50, limit=None).room is None


def test_usage_full_only_at_a_limit():
    assert Usage(used=50, limit=50).full
    assert not Usage(used=4, limit=10).full
    assert not Usage(used=900, limit=None).full


def _link(days: list[tuple[int, int, int]], **fields) -> ShortLink:
    today = date(2026, 10, 22)
    return ShortLink.model_validate(
        {
            "id": 1,
            "user_id": 1,
            "provider": "bouncy",
            "slug": "dane",
            "domain": "bouncy.ai",
            "url": "https://bouncy.ai/dane",
            "destination": "https://fanvue.com/dane",
            "status": "active",
            "created_at": 0,
            "days": [
                {"day": today - timedelta(days=ago), "views": v, "clicks": c}
                for ago, v, c in days
            ],
            **fields,
        }
    )


def test_period_sums_views_and_clicks_of_its_last_days():
    today = date(2026, 10, 22)
    link = _link([(0, 10, 4), (6, 20, 6), (7, 100, 50)])
    assert link.views(1, today) == 10
    assert link.clicks(7, today) == 10
    assert link.views(30, today) == 130
    assert link.ctr(7, today) == pytest.approx(10 / 30 * 100)
    assert _link([]).ctr(7, today) is None
    assert LinkPeriod(days=7).start(today) == date(2026, 10, 16)


def test_summary_counts_only_known_ungated_links():
    links = [_link([], adult=False), _link([], adult=None), _link([], adult=True)]
    assert LinkSummary.of(links, 7).ungated == 1


def _form(**changes) -> dict:
    return {
        "provider": "bouncy",
        "creator": "Dane",
        "target": "fanvue",
        "destination": "https://www.fanvue.com/walker_dane",
        "domain": "bouncy.ai",
        "account_ids": [1],
        **changes,
    }


@pytest.mark.parametrize(
    ("target", "url"),
    [
        ("fanvue", "https://www.fanvue.com/walker_dane?free_trial=084937f2"),
        ("stacked", "https://www.stacked.com/@danewalkerjohnson/membership"),
        ("onlyfans", "https://onlyfans.com/rachel.rosee/c124"),
    ],
)
def test_generate_form_accepts_a_profile_of_its_target(target, url):
    form = GenerateLinks.model_validate(_form(target=target, destination=url))
    assert str(form.destination) == url


def test_every_target_prefix_is_its_own_host():
    for t in targets().values():
        assert t.matches(t.prefix)
        assert t.problem(t.prefix) == f"Add the {t.label} profile after {t.prefix}"
        assert t.problem(t.prefix + "someone") is None


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"destination": "https://onlyfans.com/dane"}, "Not a Fanvue link"),
        ({"destination": "https://www.fanvue.com/"}, "Add the Fanvue profile after"),
        ({"destination": "not a link"}, "valid URL"),
        (
            {"target": "stacked", "destination": "https://www.stacked.com/dane"},
            "Add the Stacked profile after https://www.stacked.com/@",
        ),
        (
            {"target": "onlyfans", "destination": "https://www.stacked.com/@dane"},
            "Not a OnlyFans link",
        ),
        ({"domain": "oopsie.bio"}, "Bouncy has no domain oopsie.bio"),
        ({"provider": "linktree"}, "Unknown link provider"),
        ({"target": "patreon"}, "Unknown target page"),
        ({"creator": ""}, "Pick a model"),
        ({"account_ids": []}, "at least 1 item"),
    ],
)
def test_generate_form_rejects(changes, message):
    with pytest.raises(ValidationError, match=message):
        GenerateLinks.model_validate(_form(**changes))
