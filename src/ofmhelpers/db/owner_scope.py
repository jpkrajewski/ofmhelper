# Copyright (C) 2026 jpkrajewski

lazy from sqlalchemy import ColumnElement
lazy from sqlalchemy.orm import InstrumentedAttribute


def mine(
    column: ColumnElement[bool] | InstrumentedAttribute,
    owner: int | None,
) -> list:
    return [] if owner is None else [column == owner]
