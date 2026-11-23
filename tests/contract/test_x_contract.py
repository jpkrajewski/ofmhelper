# Copyright (C) 2026 jpkrajewski

import time
import uuid

import pytest

from ofmhelpers.integrations.x import XClient, XNotFoundError
from ofmhelpers.integrations.x.client import LOGGED_OUT_POSTS

PROFILE = {"fake": "fake_x", "real": "nasa"}
_X_LAUNCH = 1142974214


def test_a_profile_has_followers_and_its_latest_posts_with_views(backend):
    profile = XClient().profile(PROFILE[backend])
    assert profile.username == PROFILE[backend]
    assert profile.followers > 0
    assert profile.user_id.isdigit()
    assert 0 < len(profile.posts) <= LOGGED_OUT_POSTS
    for post in profile.posts:
        assert post.id.isdigit()
        assert post.views >= 0
        assert _X_LAUNCH < post.posted_at <= time.time() + 60
    assert max(p.views for p in profile.posts) > 0


def test_a_missing_profile_is_not_found(backend):
    with pytest.raises(XNotFoundError):
        XClient().profile(f"zz{uuid.uuid4().hex[:12]}")
