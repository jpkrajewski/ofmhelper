"""The one networked step of the lead hunt: reading a link-aggregator page.
Every test here stubs the HTTP call -- the contract under test is "a page
that will not load must never take down a run", not linktr.ee's uptime."""

from ofmhelpers.scraping import aggregators


def test_resolve_onlyfans_url_finds_the_link_in_the_page(monkeypatch):
    monkeypatch.setattr(
        aggregators,
        "_fetch_page",
        lambda *_args: '<a href="https://onlyfans.com/somegirl">my page</a>',
    )
    found = aggregators.resolve_onlyfans_url("https://linktr.ee/somegirl")
    assert found == "https://onlyfans.com/somegirl"


def test_resolve_onlyfans_url_returns_none_when_the_page_has_no_of_link(monkeypatch):
    monkeypatch.setattr(
        aggregators, "_fetch_page", lambda *_args: '<a href="https://tiktok.com/@x">'
    )
    assert aggregators.resolve_onlyfans_url("https://linktr.ee/somegirl") is None


def test_resolve_onlyfans_url_returns_none_when_the_page_will_not_load(monkeypatch):
    monkeypatch.setattr(aggregators, "_fetch_page", lambda *_args: None)
    assert aggregators.resolve_onlyfans_url("https://linktr.ee/somegirl") is None


def test_resolve_first_walks_until_one_page_answers(monkeypatch):
    monkeypatch.setenv("OFM_LEADS_AGGREGATOR_DELAY_S", "0")
    pages = {
        "https://linktr.ee/dead": None,
        "https://beacons.ai/live": "https://onlyfans.com/somegirl",
    }
    monkeypatch.setattr(aggregators, "resolve_onlyfans_url", pages.__getitem__)

    found, source = aggregators.resolve_first(list(pages))
    assert found == "https://onlyfans.com/somegirl"
    assert source == "https://beacons.ai/live"


def test_resolve_first_gives_up_cleanly(monkeypatch):
    monkeypatch.setenv("OFM_LEADS_AGGREGATOR_DELAY_S", "0")
    monkeypatch.setattr(aggregators, "resolve_onlyfans_url", lambda _url: None)
    assert aggregators.resolve_first(["https://linktr.ee/a"]) == (None, None)


def test_fetch_page_swallows_a_failed_request(monkeypatch):
    def explode(*_args, **_kwargs):
        msg = "connection reset"
        raise OSError(msg)

    monkeypatch.setattr(aggregators.requests, "get", explode)
    assert aggregators._fetch_page("https://linktr.ee/a", 1.0, 1000) is None
