"""The qualify step: follower band, confidence tiers, and the fault
isolation that keeps one bad profile from ending a run."""

import pytest

from ofmhelpers.scraping import lead_hunt
from ofmhelpers.scraping.models import Confidence, InstagramProfile


def make_profile(**overrides) -> InstagramProfile:
    return InstagramProfile.model_validate(
        {"username": "somegirl", "followers": 4200, **overrides}
    )


def test_from_apify_normalizes_both_external_url_shapes():
    profile = InstagramProfile.from_apify(
        {
            "username": "somegirl",
            "fullName": "Some Girl",
            "followersCount": 4200,
            "biography": "dm me",
            "externalUrls": [{"url": "https://linktr.ee/somegirl"}],
            "externalUrl": "https://beacons.ai/somegirl",
            "private": False,
        }
    )
    assert profile.external_urls == [
        "https://linktr.ee/somegirl",
        "https://beacons.ai/somegirl",
    ]
    assert profile.followers == 4200
    assert profile.profile_url == "https://www.instagram.com/somegirl/"


def test_from_apify_does_not_duplicate_a_repeated_external_url():
    profile = InstagramProfile.from_apify(
        {
            "username": "somegirl",
            "followersCount": 10,
            "externalUrls": [{"url": "https://linktr.ee/somegirl"}],
            "externalUrl": "https://linktr.ee/somegirl",
        }
    )
    assert profile.external_urls == ["https://linktr.ee/somegirl"]


@pytest.mark.parametrize(
    ("followers", "expected"),
    [
        (0, True),
        (9_999, True),
        # Exclusive at the top: 10k exactly is not "less than 10k".
        (10_000, False),
        (250_000, False),
        (None, False),
    ],
)
def test_in_follower_band(followers, expected):
    assert lead_hunt.in_follower_band(make_profile(followers=followers)) is expected


def test_qualify_direct_link_is_certain():
    profile = make_profile(external_urls=["https://onlyfans.com/somegirl"])
    lead = lead_hunt.qualify(profile)
    assert lead is not None
    assert lead.confidence is Confidence.CERTAIN
    assert lead.onlyfans_url == "https://onlyfans.com/somegirl"


def test_qualify_resolved_aggregator_is_certain(monkeypatch):
    monkeypatch.setattr(
        lead_hunt,
        "resolve_first",
        lambda urls: ("https://onlyfans.com/somegirl", urls[0]),
    )
    lead = lead_hunt.qualify(make_profile(biography="all my links linktr.ee/somegirl"))
    assert lead is not None
    assert lead.confidence is Confidence.CERTAIN
    assert lead.onlyfans_url == "https://onlyfans.com/somegirl"
    assert "linktr.ee/somegirl" in lead.evidence


def test_qualify_unresolved_aggregator_is_likely(monkeypatch):
    monkeypatch.setattr(lead_hunt, "resolve_first", lambda _urls: (None, None))
    lead = lead_hunt.qualify(make_profile(biography="linktr.ee/somegirl"))
    assert lead is not None
    assert lead.confidence is Confidence.LIKELY
    assert lead.onlyfans_url is None


def test_qualify_skips_the_fetch_when_resolution_is_off(monkeypatch):
    monkeypatch.setenv("OFM_LEADS_RESOLVE_AGGREGATORS", "false")

    def explode(_urls):
        msg = "must not fetch when resolution is disabled"
        raise AssertionError(msg)

    monkeypatch.setattr(lead_hunt, "resolve_first", explode)
    lead = lead_hunt.qualify(make_profile(biography="linktr.ee/somegirl"))
    assert lead is not None
    assert lead.confidence is Confidence.LIKELY


def test_qualify_mention_only_is_likely():
    lead = lead_hunt.qualify(make_profile(biography="OnlyFans model, dm for link"))
    assert lead is not None
    assert lead.confidence is Confidence.LIKELY
    assert lead.onlyfans_url is None


def test_qualify_rejects_an_account_with_no_signal():
    assert lead_hunt.qualify(make_profile(biography="dog mom, coffee")) is None


def test_qualify_rejects_an_account_over_the_ceiling():
    profile = make_profile(
        followers=50_000, external_urls=["https://onlyfans.com/somegirl"]
    )
    assert lead_hunt.qualify(profile) is None


def test_qualify_all_ranks_certain_first_then_by_followers(monkeypatch):
    monkeypatch.setattr(lead_hunt, "resolve_first", lambda _urls: (None, None))
    profiles = [
        make_profile(
            username="likely_big", followers=9_000, biography="OnlyFans model, dm me"
        ),
        make_profile(
            username="certain_small",
            followers=100,
            external_urls=["https://onlyfans.com/a"],
        ),
        make_profile(
            username="certain_big",
            followers=8_000,
            external_urls=["https://onlyfans.com/b"],
        ),
    ]
    ranked = [lead.profile.username for lead in lead_hunt.qualify_all(profiles)]
    assert ranked == ["certain_big", "certain_small", "likely_big"]


def test_qualify_all_drops_only_the_row_that_blew_up(monkeypatch):
    def explode_on_one(profile):
        if profile.username == "boom":
            msg = "aggregator exploded"
            raise RuntimeError(msg)
        return lead_hunt.Lead(
            profile=profile, confidence=Confidence.CERTAIN, evidence="stub"
        )

    monkeypatch.setattr(lead_hunt, "qualify", explode_on_one)
    profiles = [make_profile(username="boom"), make_profile(username="fine")]
    leads = lead_hunt.qualify_all(profiles)
    assert [lead.profile.username for lead in leads] == ["fine"]


def test_hunt_without_a_key_refuses_rather_than_running(monkeypatch):
    monkeypatch.setenv("OFM_APIFY_API_KEYS", "")
    with pytest.raises(RuntimeError, match="No Apify API key"):
        lead_hunt.hunt(["ofmodel"])


def test_discover_usernames_dedupes_and_caps(monkeypatch):
    monkeypatch.setenv("OFM_LEADS_MAX_PROFILES_PER_RUN", "2")
    monkeypatch.setattr(
        lead_hunt,
        "run_actor",
        lambda **_kwargs: [
            {"ownerUsername": "Alpha"},
            {"ownerUsername": "alpha"},
            {"username": "beta"},
            {"ownerUsername": "gamma"},
            {"caption": "no owner at all"},
        ],
    )
    assert lead_hunt.discover_usernames(None, ["ofmodel"]) == ["alpha", "beta"]


def test_fetch_profiles_skips_an_unparseable_item(monkeypatch):
    monkeypatch.setattr(
        lead_hunt,
        "run_actor",
        lambda **_kwargs: [
            {"username": "good", "followersCount": 500},
            {"username": "no_followers"},
            "not even a dict",
        ],
    )
    profiles = lead_hunt.fetch_profiles(None, ["good", "no_followers"])
    assert [p.username for p in profiles] == ["good"]


def test_fetch_profiles_makes_no_call_for_an_empty_list(monkeypatch):
    def explode(**_kwargs):
        msg = "must not call the actor with no usernames"
        raise AssertionError(msg)

    monkeypatch.setattr(lead_hunt, "run_actor", explode)
    assert lead_hunt.fetch_profiles(None, []) == []


def test_prepare_hashtag_input_strips_the_leading_hash():
    raw = lead_hunt.prepare_hashtag_input(["#ofmodel", "ofpromo"], 50)
    assert raw["hashtags"] == ["ofmodel", "ofpromo"]
    assert raw["resultsLimit"] == 50
