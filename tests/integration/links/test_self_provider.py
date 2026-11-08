"""The self-hosted link provider ("self"): it is always keyed, serves its own
rows as a LinkProvider, and reads its numbers from the click log by day and by
hour. No HTTP, so these run straight on the database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from signin import make_user

from ofmhelpers.db.models import SelfLinkClickRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import self_links as self_store
from ofmhelpers.db.stores import short_links
from ofmhelpers.integrations.short_links import NewLink
from ofmhelpers.integrations.short_links.providers import get_link_provider
from ofmhelpers.schemas.links import utc_today

if TYPE_CHECKING:
    from ofmhelpers.integrations.short_links.providers.base import LinkProvider


def _client(user_id: int) -> LinkProvider:
    provider = short_links.client(user_id, "self")
    assert provider is not None
    return provider


def _make_live_link(user_id: int, slug: str, host: str, destination: str) -> str:
    """Generate one self link the way /links would, and return its remote id."""
    [link_id] = short_links.add_creating(
        user_id,
        "self",
        [
            {
                "slug": slug,
                "domain": host,
                "destination": destination,
                "target": "onlyfans",
                "account_id": None,
                "creator": "",
            }
        ],
    )
    made = _client(user_id).create(
        NewLink(slug=slug, domain=host, destination=destination)
    )
    short_links.created(link_id, made)
    return made.remote_id


def _click(link_id: int, kind: str, when: datetime, **geo: str) -> None:
    with session_scope() as s:
        s.add(SelfLinkClickRow(link_id=link_id, ts=when.timestamp(), kind=kind, **geo))


def test_self_is_always_keyed_for_every_user():
    user = make_user()
    assert short_links.api_key(user.id, "self") == str(user.id)
    assert get_link_provider("self").local_key(user.id) == str(user.id)
    provider = _client(user.id)
    assert type(provider).__name__ == "SelfProvider"
    assert provider.api_key == str(user.id)


def test_create_lists_a_live_gated_link():
    user = make_user()
    self_store.add_domain("mybridge.test", None)
    rid = _make_live_link(user.id, "dany", "mybridge.test", "https://onlyfans.com/dany")

    provider = _client(user.id)
    links = provider.list_links()
    assert [x.slug for x in links] == ["dany"]
    link = links[0]
    assert link.remote_id == rid == "mybridge.test/dany"
    assert link.url == "https://mybridge.test/dany"
    assert link.active is True
    assert link.adult is True
    usage = provider.usage()
    assert usage.used == 1
    assert usage.limit is None


def test_daily_and_hourly_come_from_the_click_log():
    user = make_user()
    self_store.add_domain("mybridge.test", None)
    rid = _make_live_link(user.id, "dany", "mybridge.test", "https://onlyfans.com/dany")
    link_id = self_store._remote_to_id(user.id)[rid]

    noon = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    five_days = noon - timedelta(days=5)
    # Today: 2 landings, 1 continue-through. Five days ago: 3 and 2.
    _click(link_id, "view", noon)
    _click(link_id, "view", noon.replace(minute=5))
    _click(link_id, "click", noon.replace(minute=6), country="US")
    _click(link_id, "view", five_days)
    _click(link_id, "view", five_days.replace(minute=1))
    _click(link_id, "view", five_days.replace(minute=2))
    _click(link_id, "click", five_days.replace(minute=3))
    _click(link_id, "click", five_days.replace(minute=4))

    today = utc_today()
    provider = _client(user.id)
    days = provider.daily([rid], today - timedelta(days=30), today)[rid]
    assert [(d.views, d.clicks) for d in days] == [(3, 2), (2, 1)]

    hours = provider.hourly([rid], today)[rid]
    assert len(hours) == 1
    assert (hours[0].views, hours[0].clicks) == (2, 1)
    assert hours[0].hour.hour == 12


def test_delete_drops_it_from_the_listing():
    user = make_user()
    self_store.add_domain("mybridge.test", None)
    rid = _make_live_link(user.id, "dany", "mybridge.test", "https://onlyfans.com/dany")
    provider = _client(user.id)
    link_id = self_store._remote_to_id(user.id)[rid]

    short_links.set_status([link_id], "deleting")
    provider.delete(rid)
    assert provider.list_links() == []
