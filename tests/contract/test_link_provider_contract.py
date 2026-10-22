"""Link providers (Oopsie, Bouncy): what /links reads and makes, on the
fakes and on the real accounts (the `*_API_KEY_ADMIN` keys in .env).

Reads only, except the Bouncy round trip: one throwaway link on bouncy.ai,
18+ gated, deleted again (Bouncy is free per link; the Oopsie plan has a
page quota, so its making is checked on the fake only)."""

import uuid
from datetime import timedelta

import pytest

from ofmhelpers.config import settings
from ofmhelpers.links import NewLink, provider_infos
from ofmhelpers.links.fake import (
    BOUNCY_FAKE_KEY,
    OOPSIE_FAKE_KEY,
    fake_bouncy,
    fake_oopsie,
)
from ofmhelpers.links.providers import get_link_provider
from ofmhelpers.utils import outbound
from ofmhelpers.web.schemas.links import utc_today

PROVIDERS = ["oopsie", "bouncy"]
_FAKE_KEYS = {"oopsie": OOPSIE_FAKE_KEY, "bouncy": BOUNCY_FAKE_KEY}
# What Bouncy's 18+ page says (its "Yes, I'm 18+" button is drawn by JS).
_BOUNCY_GATE = "Age verification required"
_PHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def _seed(provider: str) -> None:
    """The fake account: one link with traffic yesterday and today."""
    today = utc_today()
    if provider == "oopsie":
        fake = fake_oopsie()
        fake.add_page("seed-page", "https://onlyfans.com/seed")
        link_id = "seed-page"
    else:
        fake = fake_bouncy()
        link_id = fake.add_link("seed", "https://fanvue.com/seed")
    fake.set_traffic(link_id, today, 10, 7)
    fake.set_traffic(link_id, today - timedelta(days=1), 4, 1)


def _client(name: str, backend: str):
    if backend == "fake":
        _seed(name)
        key = _FAKE_KEYS[name]
    else:
        key = settings.links.admin_key(name)
        assert key, f"{name.upper()}_API_KEY_ADMIN is not set in .env"
    return get_link_provider(name)(key)


@pytest.fixture(params=PROVIDERS)
def provider(request, backend):
    return _client(request.param, backend)


def test_usage_is_a_quota(provider):
    usage = provider.usage()
    assert usage.used >= 0
    assert usage.limit is None or usage.limit > 0


def test_links_are_listed_with_address_destination_and_gate(provider):
    links = provider.list_links()
    assert links
    for link in links:
        assert link.url == f"https://{link.domain}/{link.slug}"
        assert link.destination.startswith("http")
        assert link.remote_id


def test_oopsie_says_whether_a_page_is_gated(backend):
    assert all(
        isinstance(x.adult, bool) for x in _client("oopsie", backend).list_links()
    )


def test_bouncy_never_says(backend):
    assert all(x.adult is None for x in _client("bouncy", backend).list_links())


def test_daily_numbers_fall_in_the_range(provider):
    end = utc_today()
    start = end - timedelta(days=89)
    ids = [x.remote_id for x in provider.list_links()][:3]
    found = provider.daily(ids, start, end)
    assert set(found) <= set(ids)
    for days in found.values():
        assert all(start <= d.day <= end for d in days)
        assert all(d.views >= 0 and d.clicks >= 0 for d in days)
        assert len({d.day for d in days}) == len(days)


@pytest.mark.parametrize("name", PROVIDERS)
def test_fake_reports_the_seeded_traffic(name, fake_externals):
    provider = _client(name, "fake")
    end = utc_today()
    [link] = provider.list_links()
    days = provider.daily([link.remote_id], end - timedelta(days=6), end)[
        link.remote_id
    ]
    assert sum(d.views for d in days) == 14
    assert sum(d.clicks for d in days) == 8


def _round_trip(provider) -> None:
    info = provider_infos()[provider.key]
    slug = f"ofmh-contract-{uuid.uuid4().hex[:8]}"
    made = provider.create(
        NewLink(
            slug=slug,
            domain=info.default_domain,
            destination="https://example.com/",
            title="contract test",
        )
    )
    try:
        assert made.slug == slug
        assert made.adult is True
        assert made.url == f"https://{info.default_domain}/{slug}"
        assert slug in {x.slug for x in provider.list_links()}
    finally:
        provider.delete(made.remote_id)
    assert slug not in {x.slug for x in provider.list_links()}


@pytest.mark.parametrize("key", PROVIDERS)
def test_fake_makes_gated_links_and_deletes_them(key, fake_externals):
    _round_trip(get_link_provider(key)(_FAKE_KEYS[key]))
    if key == "bouncy":
        assert fake_bouncy().adult == set()


@pytest.mark.live
def test_real_bouncy_link_shows_the_18_plus_page(real_env):
    """The undocumented REST fields (`forceAdultWarning`) still work: the
    made link's page asks for the age first."""
    provider = get_link_provider("bouncy")(settings.links.admin_key("bouncy"))
    slug = f"ofmh-contract-{uuid.uuid4().hex[:8]}"
    made = provider.create(
        NewLink(slug=slug, domain="bouncy.ai", destination="https://example.com/")
    )
    try:
        assert made.active, "Bouncy's active link limit is reached"
        page = outbound.get(made.url, headers={"User-Agent": _PHONE}, timeout=30)
        assert _BOUNCY_GATE in page.text
    finally:
        provider.delete(made.remote_id)
    assert slug not in {x.slug for x in provider.list_links()}
