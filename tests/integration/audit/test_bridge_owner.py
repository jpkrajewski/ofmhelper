"""A public bridge address answers only with a link of the host's current
owner (or of a shared host): a removed host re-added by someone else serves
none of the earlier owner's links, and two owners never hold one self address."""

import pytest
from fastapi.testclient import TestClient
from signin import make_user
from sqlalchemy import select

from ofmhelpers.db.models import SelfLinkClickRow
from ofmhelpers.db.repositories import DuplicateError
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import self_links, short_links
from ofmhelpers.features.links import keys as link_keys
from ofmhelpers.features.links import self_page
from ofmhelpers.integrations.short_links import NewLink
from ofmhelpers.schemas.links import PlannedLink
from ofmhelpers.web.main import app

HOST = "reclaim.test"
IG_UA = "Mozilla/5.0 (iPhone) Instagram 300.0"


def _link(user_id: int, slug: str, destination: str) -> int:
    [link_id] = short_links.add_creating(
        user_id,
        "self",
        [
            PlannedLink(
                slug=slug,
                domain=HOST,
                destination=destination,
                target="onlyfans",
                account_id=None,
                creator="",
            )
        ],
    )
    provider = link_keys.client(user_id, "self")
    assert provider is not None
    short_links.created(
        link_id,
        provider.create(NewLink(slug=slug, domain=HOST, destination=destination)),
    )
    return link_id


def _domain_id(owner: int) -> int:
    [domain] = [d for d in self_links.domains(owner) if d.host == HOST]
    return domain.id


def _views(link_id: int) -> int:
    with session_scope() as s:
        return len(
            list(
                s.execute(
                    select(SelfLinkClickRow).where(
                        SelfLinkClickRow.link_id == link_id,
                        SelfLinkClickRow.kind == "view",
                    )
                ).scalars()
            )
        )


@pytest.fixture
def bridge():
    c = TestClient(app, base_url=f"https://{HOST}")
    yield c
    c.close()


def test_a_removed_host_re_added_by_another_owner_serves_none_of_the_old_links(bridge):
    ann = make_user("ann-bridge@example.com")
    bob = make_user("bob-bridge@example.com")
    self_page.add_domain(HOST, ann.id)
    ann_link = _link(ann.id, "dany", "https://onlyfans.com/ann")
    assert bridge.get("/dany", headers={"user-agent": IG_UA}).status_code == 200

    self_page.delete_domain(_domain_id(ann.id), ann.id)
    self_page.add_domain(HOST, bob.id)

    page = bridge.get("/dany", headers={"user-agent": IG_UA})
    assert page.status_code == 404
    assert "onlyfans.com/ann" not in page.text
    # Ann's row is kept, only switched off.
    [row] = [x for x in short_links.list_links() if x.id == ann_link]
    assert row.status == "inactive"

    # Her switched-off link still holds the address (one address, one link:
    # owner 2026-11-18), so Bob's is numbered past it.
    assert "dany" in short_links.taken_slugs(bob.id, "self", HOST)
    with pytest.raises(DuplicateError, match="already taken"):
        _link(bob.id, "dany", "https://onlyfans.com/bob")
    bob_link = _link(bob.id, "dany-2", "https://onlyfans.com/bob")
    page = bridge.get("/dany-2", headers={"user-agent": IG_UA})
    assert page.status_code == 200
    assert '"https://onlyfans.com/bob"' in page.text
    assert bridge.get("/dany", headers={"user-agent": IG_UA}).status_code == 404
    assert _views(bob_link) == 1
    assert _views(ann_link) == 1  # only her own earlier visit


def test_a_shared_host_gives_one_self_address_to_one_owner():
    ann = make_user("ann-bridge@example.com")
    bob = make_user("bob-bridge@example.com")
    self_links.add_domain(HOST, None)
    assert "dany" not in short_links.taken_slugs(bob.id, "self", HOST)
    _link(ann.id, "dany", "https://onlyfans.com/ann")
    assert "dany" in short_links.taken_slugs(bob.id, "self", HOST)
    assert "dany" in short_links.taken_slugs(ann.id, "self", HOST)
    # Two Generates racing past the check: the database refuses the second.
    with pytest.raises(DuplicateError, match="already taken"):
        _link(bob.id, "dany", "https://onlyfans.com/bob")


def test_a_host_someone_holds_cannot_be_added_and_whose_is_not_said():
    ann = make_user("ann-bridge@example.com")
    bob = make_user("bob-bridge@example.com")
    self_page.add_domain(HOST, ann.id)

    with pytest.raises(DuplicateError) as refused:
        self_page.add_domain(HOST, bob.id)

    assert str(refused.value) == "This domain can't be added"
