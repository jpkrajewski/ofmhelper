# Copyright (C) 2026 jpkrajewski

from typing import Any


def mine(column: Any, owner: int | None) -> list:
    return [] if owner is None else [column == owner]
