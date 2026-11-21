import time

import pytest

from ofmhelpers.config import settings
from ofmhelpers.integrations.apify import ApifyClient, richest
from ofmhelpers.integrations.socials import ScanTarget
from ofmhelpers.integrations.socials.platforms.x_apify import ApifyXSource

HANDLE = {"fake": "fake_x", "real": "nasa"}


@pytest.fixture
def client(paid_backend, monkeypatch) -> ApifyClient:
    if paid_backend == "fake":
        monkeypatch.setenv("APIFY_API_KEYS", "fake-apify-key")
    keys = settings.viral.api_keys
    assert keys, "APIFY_API_KEYS is not set in .env"
    return richest(keys)


def test_an_account_read_gives_its_own_posts_in_the_window(client, paid_backend):
    assert client.remaining_usd() > 0
    since = time.time() - settings.accounts.apify_days * 86400
    answers = ApifyXSource().scan([ScanTarget(key="1", handle=HANDLE[paid_backend])])
    stats = answers.stats["1"]
    assert stats.profile.handle == HANDLE[paid_backend]
    assert stats.profile.followers > 0
    assert stats.profile.account_id.isdigit()
    assert stats.posts
    assert all(p.posted_at >= since - 60 for p in stats.posts)
    assert all(p.views >= 0 for p in stats.posts)
    assert all(p.id.isdigit() for p in stats.posts)
