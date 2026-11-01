"""Apify's Instagram reel scraper, as /viral reads it: on the fake, and on
the real accounts (`APIFY_API_KEYS` in .env; a run of 3 reels costs under
a cent)."""

import pytest

from ofmhelpers.apify import RunStatus, richest
from ofmhelpers.config import settings
from ofmhelpers.viral import ViralError, most_played, parse
from ofmhelpers.viral.media import has_audio
from ofmhelpers.viral.service import _download, reel_input

# A public profile with reels on each side.
_PROFILE = {"fake": "sheep_love33", "real": "natgeo"}
_MISSING = "no-such-profile-ofmhelpers-0x1"
_RUN_TIMEOUT_S = 300


@pytest.fixture
def client(backend, monkeypatch):
    if backend == "fake":
        monkeypatch.setenv("APIFY_API_KEYS", "fake-apify-key")
    keys = settings.viral.api_keys
    assert keys, "APIFY_API_KEYS is not set in .env"
    return richest(keys)


def test_an_account_reads_its_credit(client):
    assert client.remaining_usd() >= 0


def test_a_profiles_reels_come_with_play_counts_and_sound(client, backend, tmp_path):
    run = client.run(
        settings.viral.actor, reel_input(_PROFILE[backend], 3), _RUN_TIMEOUT_S
    )
    assert run.status == RunStatus.SUCCEEDED
    assert run.cost_usd > 0
    reels = parse(_PROFILE[backend], client.items(run))
    assert len(reels) == 3
    assert all(r.code and r.video_url and r.plays > 0 for r in reels)

    top = most_played(reels, 1)[0]
    saved = _download(top, tmp_path)
    assert saved.stat().st_size > 0
    assert has_audio(saved)


def test_a_missing_profile_gives_no_reels(client):
    run = client.run(settings.viral.actor, reel_input(_MISSING, 3), _RUN_TIMEOUT_S)
    assert run.status == RunStatus.SUCCEEDED
    with pytest.raises(ViralError):
        parse(_MISSING, client.items(run))
