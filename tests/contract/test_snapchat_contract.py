import time
import uuid

import pytest

from ofmhelpers.integrations.snapchat import (
    SnapchatClient,
    SnapchatNotFoundError,
    SnapchatNotPublicError,
)

PUBLIC = {"fake": "fake_snap", "real": "kyliejenner"}
NOT_PUBLIC = {"fake": "fake_hidden", "real": "teamsnapchat"}
_SNAPCHAT_SPOTLIGHT_LAUNCH = 1606780800


def test_a_public_profile_has_subscribers_and_spotlight_views(backend):
    profile = SnapchatClient().profile(PUBLIC[backend])
    assert profile.username == PUBLIC[backend]
    assert profile.subscribers > 0
    assert profile.user_id
    assert profile.posts
    for post in profile.posts:
        assert post.id
        assert post.views >= 0
        assert _SNAPCHAT_SPOTLIGHT_LAUNCH < post.posted_at <= time.time() + 60
        assert (
            post.url
            == f"https://www.snapchat.com/@{profile.username}/spotlight/{post.id}"
        )


def test_a_profile_that_is_not_public_says_so(backend):
    with pytest.raises(SnapchatNotPublicError):
        SnapchatClient().profile(NOT_PUBLIC[backend])


def test_a_missing_profile_is_not_found(backend):
    with pytest.raises(SnapchatNotFoundError):
        SnapchatClient().profile(f"zz{uuid.uuid4().hex[:12]}")
