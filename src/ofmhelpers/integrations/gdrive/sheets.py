"""A Google Sheet as Drive exports it (xlsx), read into `DriveSheet`.

xlsx, not CSV: a date cell arrives as a real date whatever format the sheet
shows it in (CSV carries the displayed text, `12/11/2026`, which reads two
ways), and every tab comes along (CSV: the first only). Checked live
2026-11-12."""

import io
from datetime import date, datetime
from typing import Any

lazy from openpyxl import load_workbook
lazy from openpyxl.cell.cell import Cell

from ofmhelpers.integrations.gdrive.models import DriveSheet, SheetTab

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
        # A label over a link ("reel 3"): the address is what was meant.
        return target.strip()
    return text or None


def _rows(cells: list[list[date | str | None]]) -> list[list[date | str | None]]:
    """Without the empty rows a tab's formatting leaves after the last filled
    one."""
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
        ]
    )
