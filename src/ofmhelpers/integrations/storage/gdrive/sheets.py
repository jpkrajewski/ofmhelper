# Copyright (C) 2026 jpkrajewski

import io
from datetime import date, datetime
from typing import Any

lazy from openpyxl import load_workbook
lazy from openpyxl.cell.cell import Cell

from ofmhelpers.integrations.storage.models import (
    DriveSheet,
    SheetTab,
)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_LINK = ("http://", "https://")


def _value(cell: Cell) -> date | str | None:
    value: Any = cell.value
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    target = cell.hyperlink.target if cell.hyperlink else None
    if target and not text.startswith(_LINK):
        return target.strip()
    return text or None


def _rows(cells: list[list[date | str | None]]) -> list[list[date | str | None]]:
    last = max((i for i, row in enumerate(cells) if any(row)), default=-1)
    return cells[: last + 1]


def read_xlsx(content: bytes) -> DriveSheet:
    book = load_workbook(io.BytesIO(content), data_only=True)
    return DriveSheet(
        tabs=[
            SheetTab(
                name=tab.title,
                rows=_rows([[_value(c) for c in row] for row in tab.iter_rows()]),
            )
            for tab in book.worksheets
        ],
    )
