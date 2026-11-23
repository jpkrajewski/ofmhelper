# Copyright (C) 2026 jpkrajewski

from typing import Any

lazy from sqlalchemy import ColumnElement
lazy from sqlalchemy.orm import InstrumentedAttribute


def mine(
    column: ColumnElement[bool] | InstrumentedAttribute[Any],
    owner: int | None,
) -> list[ColumnElement[bool]]:
    return [] if owner is None else [column == owner]
