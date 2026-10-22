"""The per-user row filter every owned table's repository shares."""

from __future__ import annotations

from typing import Any


def mine(column: Any, owner: int | None) -> list:
    """The WHERE clause limiting `column`'s table to `owner`'s rows (none:
    everyone's, for system work such as the sweeps;
    a request always passes its user's id, admins included). Writes on a user's own rows pass their
    id, never None."""
    return [] if owner is None else [column == owner]
