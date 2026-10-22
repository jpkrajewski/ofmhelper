"""/links: links on Oopsie and Bouncy (faked at the wire), made one per
Instagram account of a model, always 18+ gated; synced with their daily
views and clicks; shown on /accounts next to each account's phones. Jobs
run inline (the suite's RQ is synchronous)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from signin import sign_in, sign_in_agency

from ofmhelpers.links.fake import (
    BOUNCY_FAKE_KEY,
    OOPSIE_FAKE_KEY,
    FakeBouncy,
    fake_bouncy,
    fake_oopsie,
)
from ofmhelpers.utils.outbound import FakeReply
from ofmhelpers.web import link_sync
from ofmhelpers.web.db.models import ShortLinkRow
from ofmhelpers.web.db.session import session_scope
from ofmhelpers.web.main import app
from ofmhelpers.web.schemas import Role, UserForm
from ofmhelpers.web.schemas.links import utc_today
from ofmhelpers.web.stores import short_links, users
from ofmhelpers.web.stores.tracked_accounts import list_accounts, track

FANVUE = "https://www.fanvue.com/walker_dane"


@pytest.fixture(autouse=True)
def _fakes(fake_externals, monkeypatch):
    monkeypatch.setenv("OOPSIE_API_KEY_ADMIN", OOPSIE_FAKE_KEY)
    monkeypatch.setenv("BOUNCY_API_KEY_ADMIN", BOUNCY_FAKE_KEY)


@pytest.fixture
def client():
    c = TestClient(app)
    yield c
    c.close()


@pytest.fixture
def admin(client):
    """The agency admin: the one the .env keys run for."""
    return sign_in_agency(client)


def _accounts(user_id: int, creator: str, *handles: str) -> list[int]:
    for handle in handles:
        track("instagram", handle, creator, user_id=user_id)
    ids = {a.handle: a.id for a in list_accounts(user_id)}
    return [ids[h] for h in handles]


def _generate(client, account_ids, **changes):
    body = {
        "provider": "bouncy",
        "creator": "Dane",
        "target": "fanvue",
        "destination": FANVUE,
        "domain": "bouncy.ai",
        "account_ids": account_ids,
        **changes,
    }
    return client.post("/links/generate", json=body)


def _links(user_id: int) -> dict[str, object]:
    return {x.slug: x for x in short_links.list_links(user_id)}


# --- the page ---------------------------------------------------------------


def test_page_lists_links_with_quota_and_nav(client, admin):
    page = client.get("/links")
    assert page.status_code == 200
    assert 'href="/links"' in page.text
    assert "No links yet" in page.text
    assert "Oopsie links" in page.text
    assert "Bouncy links" in page.text


def test_a_user_without_their_own_key_gets_no_env_key(client):
    user = sign_in(client)
    assert [s.has_key for s in short_links.provider_states(user.id)] == [False, False]
    page = client.get("/links")
    assert "No Oopsie API key" in page.text
    ids = _accounts(user.id, "Dane", "dane.walker")
    response = _generate(client, ids)
    assert response.status_code == 400
    assert response.json()["detail"] == "Add your Bouncy API key on /account first"


def test_a_user_with_their_own_key_makes_links_on_it(client):
    user = sign_in(client)
    users.set_api_key(user.id, "bouncy", BOUNCY_FAKE_KEY)
    ids = _accounts(user.id, "Dane", "dane.walker")
    assert _generate(client, ids).json() == {"count": 1}
    assert _links(user.id)["dane-walker"].status == "active"


# --- generating -------------------------------------------------------------


def test_bouncy_links_are_made_per_account_and_18_plus_gated(client, admin):
    ids = _accounts(admin.id, "Dane", "dane.walker", "dane_2nd")
    response = _generate(client, ids)
    assert response.json() == {"count": 2}
    links = _links(admin.id)
    assert set(links) == {"dane-walker", "dane_2nd"}
    for link in links.values():
        assert link.status == "active"
        assert link.adult is True
        assert link.target == "fanvue"
        assert link.creator == "Dane"
        assert link.url == f"https://bouncy.ai/{link.slug}"
    assert fake_bouncy().adult == {"dane-walker", "dane_2nd"}
    assert {x.account_handle for x in links.values()} == {"dane.walker", "dane_2nd"}


def test_oopsie_links_are_direct_deeplinks_with_the_age_gate(client, admin):
    ids = _accounts(admin.id, "Rachel", "rachel.rose")
    response = _generate(
        client,
        ids,
        provider="oopsie",
        domain="cutie.bio",
        target="onlyfans",
        destination="https://onlyfans.com/rachel.rosee/c124",
        creator="Rachel",
    )
    assert response.status_code == 200
    page = fake_oopsie().pages["rachel.rose"]
    assert page["theme"]["age_gate"] is True
    assert page["theme"]["page_type"] == "direct"
    assert page["theme"]["preferred_public_host"] == "cutie.bio"
    assert page["theme"]["destination_url"] == "https://onlyfans.com/rachel.rosee/c124"
    assert _links(admin.id)["rachel.rose"].url == "https://cutie.bio/rachel.rose"


def test_a_slug_taken_on_the_provider_is_numbered(client, admin):
    fake_bouncy().add_link("dane-walker", "https://fanvue.com/someone-else")
    ids = _accounts(admin.id, "Dane", "dane.walker")
    _generate(client, ids)
    links = _links(admin.id)
    # The taken one is the account's too now: the sync lists it.
    assert set(links) == {"dane-walker", "dane-walker-2"}
    assert links["dane-walker-2"].adult is True
    assert (
        fake_bouncy().links["dane-walker"]["destination"]
        == "https://fanvue.com/someone-else"
    )


def test_a_second_batch_numbers_past_its_own_links(client, admin):
    ids = _accounts(admin.id, "Dane", "dane.walker")
    _generate(client, ids)
    _generate(
        client, ids, target="stacked", destination="https://www.stacked.com/@dane"
    )
    assert set(_links(admin.id)) == {"dane-walker", "dane-walker-2"}


def test_a_full_plan_fails_the_rest_with_the_providers_words(client, admin):
    fake_bouncy().set_limit(1)
    ids = _accounts(admin.id, "Dane", "a.one", "b.two", "c.three")
    _generate(client, ids)
    links = short_links.list_links(admin.id)
    assert sorted(x.status for x in links) == ["active", "failed", "failed"]
    failed = [x for x in links if x.status == "failed"]
    assert all("plan limit reached" in (x.error or "") for x in failed)
    page = client.get("/links")
    assert "Failed" in page.text


def test_a_provider_whose_plan_is_full_makes_nothing(client, admin):
    """The last sync saw the plan full: Generate refuses before any call."""
    fake_bouncy().set_limit(1)
    fake_bouncy().add_link("already", FANVUE)
    link_sync.sync_now(admin.id)
    page = client.get("/links")
    assert "Bouncy (plan full)" in page.text
    ids = _accounts(admin.id, "Dane", "dane.walker")
    response = _generate(client, ids)
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Bouncy plan is full (1 / 1): delete links there or upgrade first"
    )
    assert set(_links(admin.id)) == {"already"}


def test_no_more_links_than_bouncy_has_active_slots(client, admin):
    """Past the active limit Bouncy makes links inactive: dead on arrival."""
    for slug in ("one", "two", "three"):
        fake_bouncy().add_link(slug, FANVUE)
    link_sync.sync_now(admin.id)
    assert "Bouncy (2 left)" in client.get("/links").text
    ids = _accounts(admin.id, "Dane", "a.one", "b.two", "c.three")
    response = _generate(client, ids)
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Bouncy has room for 2 more live links (3 / 10 made, 3 / 5 active):"
        " pick fewer accounts, or free links there first"
    )
    assert _generate(client, ids[:2]).json() == {"count": 2}
    made = [x for x in short_links.list_links(admin.id) if x.account_id]
    assert {x.status for x in made} == {"active"}


def test_the_dialog_carries_each_targets_prefix_and_pattern(client, admin):
    page = client.get("/links").text
    assert 'value="stacked" data-prefix="https://www.stacked.com/@"' in page
    assert 'data-profile="^/@[A-Za-z0-9._-]+(/.*)?$"' in page


def test_a_link_whose_gate_fails_is_deleted_again(client, admin, monkeypatch):
    """Bouncy takes the link but not the 18+ fields: none goes live
    ungated."""
    original = FakeBouncy._link

    def gate_ignored(self, state, method, link_id, request):
        if method == "PUT":
            return FakeReply.json_body({"success": True, "updatedFields": []})
        return original(self, state, method, link_id, request)

    monkeypatch.setattr(FakeBouncy, "_link", gate_ignored)
    ids = _accounts(admin.id, "Dane", "dane.walker")
    _generate(client, ids)
    [row] = short_links.list_links(admin.id)
    assert row.status == "failed"
    assert row.error == "Bouncy did not take the 18+ gate on dane-walker"
    assert fake_bouncy().links == {}


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"destination": "https://onlyfans.com/dane"}, 422),
        ({"domain": "cutie.bio"}, 422),
        ({"account_ids": [999999]}, 404),
    ],
)
def test_bad_generate_requests_make_nothing(client, admin, changes, status):
    ids = _accounts(admin.id, "Dane", "dane.walker")
    response = _generate(client, changes.pop("account_ids", ids), **changes)
    assert response.status_code == status
    assert short_links.list_links(admin.id) == []


def test_at_most_max_generate_links_at_once(client, admin, monkeypatch):
    monkeypatch.setenv("OFM_LINKS_MAX_GENERATE", "1")
    ids = _accounts(admin.id, "Dane", "a.one", "b.two")
    response = _generate(client, ids)
    assert response.status_code == 400
    assert response.json()["detail"] == "At most 1 links at once"


def test_another_users_account_cannot_be_linked(client, admin):
    stranger = sign_in(TestClient(app), "stranger@example.com")
    theirs = _accounts(stranger.id, "Dane", "their.account")
    assert _generate(client, theirs).status_code == 404


# --- syncing ----------------------------------------------------------------


def test_sync_imports_links_matches_accounts_and_stores_days(client, admin):
    today = utc_today()
    [account_id] = _accounts(admin.id, "Dane", "danywalking")
    fake_oopsie().add_page("danywalking", "https://www.stacked.com/@dane/membership")
    fake_oopsie().add_page("ungated", "https://onlyfans.com/x", age_gate=False)
    fake_oopsie().set_traffic("danywalking", today, 10, 8)
    fake_oopsie().set_traffic("danywalking", today - timedelta(days=10), 90, 40)
    client.post("/links/sync")
    links = _links(admin.id)
    assert set(links) == {"danywalking", "ungated"}
    dany = links["danywalking"]
    assert dany.account_id == account_id
    assert dany.creator == "Dane"
    assert dany.target == "stacked"
    assert (dany.views(7), dany.clicks(7), dany.clicks(30)) == (10, 8, 48)
    # Links are always 18+ gated: the sync turns it on where it is off.
    assert links["ungated"].adult is True
    assert fake_oopsie().pages["ungated"]["theme"]["age_gate"] is True
    page = client.get("/links?period=30")
    assert "without the 18+ confirmation" not in page.text
    assert "48" in page.text
    [usage] = [
        s.usage for s in short_links.provider_states(admin.id) if s.key == "oopsie"
    ]
    assert (usage.used, usage.limit) == (2, 50)


def test_a_link_gone_from_the_provider_goes_from_the_list(client, admin):
    fake_oopsie().add_page("temp", "https://onlyfans.com/x")
    link_sync.sync_now(admin.id)
    fake_oopsie().remove("temp")
    link_sync.sync_now(admin.id)
    assert short_links.list_links(admin.id) == []


def test_a_link_its_job_lost_is_found_by_address_or_failed(client, admin, monkeypatch):
    ids = _accounts(admin.id, "Dane", "dane.walker", "lost.one")
    planned = [
        {"slug": s, "domain": "bouncy.ai", "destination": FANVUE, "target": "fanvue",
         "account_id": i, "creator": "Dane"}
        for s, i in zip(["dane-walker", "lost-one"], ids, strict=True)
    ]  # fmt: skip
    short_links.add_creating(admin.id, "bouncy", planned)
    with session_scope() as s:
        s.query(ShortLinkRow).update({ShortLinkRow.created_at: time.time() - 3600})
    # The job made the first one, then died.
    fake_bouncy().add_link("dane-walker", FANVUE)
    link_sync.sync_now(admin.id)
    links = _links(admin.id)
    assert links["dane-walker"].status == "active"
    assert links["dane-walker"].account_handle == "dane.walker"
    assert links["lost-one"].status == "failed"
    assert "Interrupted" in (links["lost-one"].error or "")


def test_a_provider_error_is_kept_on_the_sync(client, admin, monkeypatch):
    monkeypatch.setenv("BOUNCY_API_KEY_ADMIN", "bcy_live_pk_revoked")
    link_sync.sync_now(admin.id)
    [state] = [s for s in short_links.provider_states(admin.id) if s.key == "bouncy"]
    assert "refused the API key" in (state.error or "")
    assert "Bouncy: Bouncy refused the API key" in client.get("/links").text


def test_two_sweeps_never_claim_the_same_sync(admin):
    pairs = [(admin.id, "oopsie")]
    assert short_links.claim_due(pairs) == pairs
    assert short_links.claim_due(pairs) == []


# --- bulk actions -----------------------------------------------------------


def test_turn_on_18_plus_for_an_ungated_page(client, admin):
    fake_oopsie().add_page("ungated", "https://onlyfans.com/x", age_gate=False)
    link_sync.sync_now(admin.id)
    [link] = short_links.list_links(admin.id)
    assert client.post("/links/adult", json={"ids": [link.id]}).json() == {"count": 1}
    assert fake_oopsie().pages["ungated"]["theme"]["age_gate"] is True
    assert short_links.list_links(admin.id)[0].adult is True


def test_delete_removes_links_on_the_provider_and_here(client, admin):
    ids = _accounts(admin.id, "Dane", "dane.walker")
    _generate(client, ids)
    [link] = short_links.list_links(admin.id)
    assert client.post("/links/delete", json={"ids": [link.id]}).json() == {"count": 1}
    assert fake_bouncy().links == {}
    assert short_links.list_links(admin.id) == []


def test_a_failed_link_is_only_removed_here(client, admin):
    fake_bouncy().set_limit(0)
    ids = _accounts(admin.id, "Dane", "dane.walker")
    _generate(client, ids)
    [link] = short_links.list_links(admin.id)
    assert link.status == "failed"
    client.post("/links/delete", json={"ids": [link.id]})
    assert short_links.list_links(admin.id) == []


def test_assign_an_existing_model_only(client, admin):
    _accounts(admin.id, "Dane", "dane.walker")
    fake_oopsie().add_page("other", "https://onlyfans.com/x")
    link_sync.sync_now(admin.id)
    [link] = short_links.list_links(admin.id)
    response = client.post("/links/creator", json={"ids": [link.id], "creator": "Dane"})
    assert response.json() == {"count": 1}
    assert short_links.list_links(admin.id)[0].creator == "Dane"
    unknown = client.post(
        "/links/creator", json={"ids": [link.id], "creator": "Nobody"}
    )
    assert unknown.status_code == 404


def test_nobody_acts_on_another_users_links(client, admin):
    ids = _accounts(admin.id, "Dane", "dane.walker")
    _generate(client, ids)
    [link] = short_links.list_links(admin.id)
    stranger = TestClient(app)
    sign_in(stranger, "stranger@example.com")
    assert "dane-walker" not in stranger.get("/links").text
    assert stranger.post("/links/delete", json={"ids": [link.id]}).json() == {
        "count": 0
    }
    assert stranger.post("/links/adult", json={"ids": [link.id]}).json() == {"count": 0}
    assert fake_bouncy().links


# --- elsewhere --------------------------------------------------------------


def test_accounts_page_shows_each_accounts_links_with_clicks_and_ctr(client, admin):
    today = utc_today()
    ids = _accounts(admin.id, "Dane", "dane.walker")
    _generate(client, ids)
    fake_bouncy().set_traffic("dane-walker", today, 40, 10)
    link_sync.sync_now(admin.id)
    page = client.get("/accounts")
    assert f'href="/links?account={ids[0]}#link-' in page.text
    assert "Bouncy</span> · <strong>10</strong> clicks" in page.text
    assert "25.0% CTR" in page.text
    filtered = client.get(f"/links?account={ids[0]}")
    assert "bouncy.ai/dane-walker" in filtered.text


def test_account_page_takes_link_provider_keys_and_syncs(client):
    user = sign_in(client)
    page = client.get("/account")
    assert 'name="key_oopsie"' in page.text
    assert 'name="key_bouncy"' in page.text
    fake_oopsie().add_page("mine", "https://onlyfans.com/me")
    saved = client.post("/account/keys", data={"key_oopsie": OOPSIE_FAKE_KEY})
    assert "Oopsie key saved." in saved.text
    assert users.api_keys(user.id)["oopsie"] == OOPSIE_FAKE_KEY
    assert set(_links(user.id)) == {"mine"}


# --- ultrareview fixes ------------------------------------------------------


def test_only_the_agency_admin_runs_on_the_env_keys(client, admin):
    """Another admin (added on /users) has data of their own: the agency's
    .env keys would sync, show and let them delete the agency's links."""
    other = users.add_user(UserForm(email="second.admin@example.com", role=Role.ADMIN))
    assert short_links.api_key(other.id, "bouncy") is None
    agency = users.agency_id()
    assert short_links.api_key(agency, "bouncy") == BOUNCY_FAKE_KEY
    assert (other.id, "bouncy") not in short_links.keyed()


def test_a_link_left_ungated_is_recorded_flagged_and_gated_at_the_next_sync(
    client, admin, monkeypatch
):
    """The gate fails and so does deleting the link: it is live without the
    gate. It is kept, flagged No 18+, and the next sync gates it."""
    original = FakeBouncy._link

    def broken(self, state, method, link_id, request):
        if method in ("PUT", "DELETE"):
            return FakeReply.json_body({"error": "Service unavailable"}, 503)
        return original(self, state, method, link_id, request)

    monkeypatch.setattr(FakeBouncy, "_link", broken)
    ids = _accounts(admin.id, "Dane", "dane.walker")
    _generate(client, ids)
    # Kept and flagged (the sync's gate failed too while Bouncy is down).
    [link] = short_links.list_links(admin.id)
    assert (link.status, link.adult) == ("active", False)
    assert "No 18+" in client.get("/links").text

    monkeypatch.setattr(FakeBouncy, "_link", original)
    link_sync.sync_now(admin.id)
    assert short_links.list_links(admin.id)[0].adult is True
    assert fake_bouncy().adult == {"dane-walker"}


def test_a_numbered_slug_never_lands_on_the_owners_own(client, admin):
    """'anna' is someone else's on Bouncy; the owner already has 'anna-2'
    from an earlier Generate: the next one becomes 'anna-3', no crash."""
    fake_bouncy().add_link("anna", FANVUE)
    ids = _accounts(admin.id, "Dane", "anna")
    _generate(client, ids)
    _generate(client, ids)
    # (The fake lists the foreign "anna" in this account too.)
    slugs = {x.slug for x in short_links.list_links(admin.id) if x.account_id}
    assert slugs - {"anna"} == {"anna-2", "anna-3"}


def test_an_unexpected_error_fails_one_link_and_the_rest_go_on(
    client, admin, monkeypatch
):
    from ofmhelpers.links.providers.bouncy import BouncyProvider

    calls = {"n": 0}
    real = BouncyProvider.create

    def create(self, link):
        calls["n"] += 1
        if calls["n"] == 1:
            msg = "boom"
            raise RuntimeError(msg)
        return real(self, link)

    monkeypatch.setattr(BouncyProvider, "create", create)
    ids = _accounts(admin.id, "Dane", "a.one", "b.two")
    _generate(client, ids)
    statuses = sorted(
        (x.status, x.error or "")
        for x in short_links.list_links(admin.id)
        if x.account_id
    )
    assert statuses[0] == ("active", "")
    assert statuses[1] == ("failed", "Failed (RuntimeError): boom")


def test_a_sync_on_an_odd_answer_is_recorded_not_lost(client, admin, monkeypatch):
    from ofmhelpers.links.providers.bouncy import BouncyProvider

    def odd(self):
        missing = "pagination"
        raise KeyError(missing)

    monkeypatch.setattr(BouncyProvider, "list_links", odd)
    link_sync.sync_now(admin.id, "bouncy")
    [state] = [s for s in short_links.provider_states(admin.id) if s.key == "bouncy"]
    assert state.error == "Sync failed (KeyError): 'pagination'"
    assert short_links.claim_due([(admin.id, "bouncy")]) == []  # claim dropped


def test_a_lost_delete_goes_back_to_its_status_and_a_done_one_goes(
    client, admin, monkeypatch
):
    ids = _accounts(admin.id, "Dane", "a.one", "b.two")
    _generate(client, ids)
    links = {x.slug: x for x in short_links.list_links(admin.id) if x.account_id}
    short_links.set_status([x.id for x in links.values()], "deleting")
    with session_scope() as s:
        s.query(ShortLinkRow).update({ShortLinkRow.updated_at: time.time() - 3600})
    fake_bouncy().remove("b-two")  # its delete went through, the job then died

    link_sync.sync_now(admin.id)

    after = {x.slug: x for x in short_links.list_links(admin.id) if x.account_id}
    assert set(after) == {"a-one"}
    assert after["a-one"].status == "active"
    assert after["a-one"].error == "Delete interrupted: delete it again"


def test_one_key_waits_its_turn_across_clients(monkeypatch):
    """Oopsie allows 60 calls a minute per key: two clients (two jobs) on
    one key share the spacing."""
    from ofmhelpers.links.providers.oopsie import OopsieProvider

    waits: list[float] = []
    monkeypatch.setattr("time.sleep", waits.append)
    first, second = OopsieProvider(OOPSIE_FAKE_KEY), OopsieProvider(OOPSIE_FAKE_KEY)
    first._wait_turn(1.05)
    second._wait_turn(1.05)
    assert waits
    assert waits[-1] > 0.9
    OopsieProvider("gmp_live_another")._wait_turn(1.05)
    assert len(waits) == 1  # another key: no wait


def test_accounts_link_column_says_today_for_one_day(client, admin):
    ids = _accounts(admin.id, "Dane", "dane.walker")
    _generate(client, ids)
    page = client.get("/accounts?period=1").text
    assert "Links · today" in page
    assert "Links · 24h" not in page
