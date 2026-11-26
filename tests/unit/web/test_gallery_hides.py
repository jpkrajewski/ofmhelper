# Copyright (C) 2026 jpkrajewski

import pytest

from ofmhelpers.db.users.schema import Role, SessionUser, Viewer
from ofmhelpers.web.gallery import _hides


def _viewer(role: Role, pages: Role | None) -> Viewer:
    return Viewer(
        user=SessionUser(id=1, email="a@example.com", role=role),
        user_ids=frozenset({1}),
        team_ids=frozenset({1}),
        keys_of=None,
        pages=pages,
    )


@pytest.mark.parametrize(
    ("role", "pages", "hides"),
    [
        (Role.USER, None, True),
        (Role.ADMIN, None, True),
        (Role.VA, None, False),
        (Role.USER, Role.MANAGER, False),
        (Role.ADMIN, Role.MANAGER, False),
    ],
)
def test_hidden_cards_follow_the_access_role_not_the_employers(role, pages, hides):
    assert _hides(_viewer(role, pages)) is hides
