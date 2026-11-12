"""gdrive/sheets.py + the fake's sheets: a Google Sheet read into
`DriveSheet` (Drive's xlsx export), and found by name in a folder."""

import io
from datetime import date, datetime
from unittest.mock import MagicMock

import pytest
from openpyxl import Workbook

from ofmhelpers.integrations.gdrive import Drive, DriveError, ReadOnlyDrive
from ofmhelpers.integrations.gdrive import client as drive_client
from ofmhelpers.integrations.gdrive.fake import FakeDrive
from ofmhelpers.integrations.gdrive.models import SHEET_MIME, SheetTab
from ofmhelpers.integrations.gdrive.sheets import read_xlsx

LINK = "https://www.instagram.com/reel/abc/?cplk=xyz"
ROOT = "sheet-root"


def _xlsx(book: Workbook) -> bytes:
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_a_date_cell_is_a_date_whatever_format_the_sheet_shows():
    book = Workbook()
    tab = book.worksheets[0]
    tab.title = "Sheet1"
    tab["A1"] = datetime(2026, 11, 12)  # noqa: DTZ001 -- xlsx has no time zones
    tab["A1"].number_format = "dd/MM/yyyy"
    tab["B1"] = date(2026, 11, 13)
    tab["B1"].number_format = "yyyy-mm-dd"
    tab["A2"] = LINK

    (read,) = read_xlsx(_xlsx(book)).tabs

    assert read.name == "Sheet1"
    assert read.rows == [[date(2026, 11, 12), date(2026, 11, 13)], [LINK, None]]


def test_a_label_over_a_link_reads_as_the_link_and_a_shown_address_as_itself():
    book = Workbook()
    tab = book.worksheets[0]
    tab["A1"] = "reel 3"
    tab["A1"].hyperlink = LINK
    tab["A2"] = f"  {LINK}  "
    tab["A2"].hyperlink = "https://elsewhere.example/"
    tab["A3"] = 70000

    (read,) = read_xlsx(_xlsx(book)).tabs

    assert read.rows == [[LINK], [LINK], ["70000"]]


def test_rows_end_at_the_last_filled_one_and_every_tab_comes():
    book = Workbook()
    first = book.worksheets[0]
    first["A1"] = "top"
    first["A3"] = "last"
    # Formatting far below the data: what makes Drive's used range 46 rows.
    first["A40"].number_format = "dd/MM/yyyy"
    second = book.create_sheet("Other")
    second["B2"] = "x"
    book.create_sheet("Empty")

    one, two, empty = read_xlsx(_xlsx(book)).tabs

    assert one.rows == [["top"], [None], ["last"]]
    assert (two.name, two.rows) == ("Other", [[None, None], [None, "x"]])
    assert (empty.name, empty.rows) == ("Empty", [])


# --- the fake Drive ---------------------------------------------------------------


@pytest.fixture
def drive() -> FakeDrive:
    fake = FakeDrive()
    fake.seed_folder("ALL ACCOUNTS", item_id=ROOT, shared=True)
    return fake


def test_a_sheet_is_found_by_name_and_read(drive):
    tabs = [SheetTab(name="Sheet1", rows=[[date(2026, 11, 12)], [LINK]])]
    made = drive.add_sheet(ROOT, "content_links", tabs)

    found = drive.find_file(ROOT, "content_links")

    assert found is not None
    assert (found.id, found.mime_type) == (made, SHEET_MIME)
    assert drive.sheet(made).tabs == tabs
    assert drive.calls == ["find_file", "sheet"]
    assert drive.writes == []


def test_find_file_takes_the_oldest_of_a_name_and_never_a_folder_or_trash(drive):
    drive.create_folder(ROOT, "content_links")
    assert drive.find_file(ROOT, "content_links") is None

    first = drive.add_file(ROOT, "content_links", SHEET_MIME)
    drive.add_file(ROOT, "content_links", SHEET_MIME)
    assert drive.find_file(ROOT, "content_links").id == first

    drive.trash(first)
    assert drive.find_file(ROOT, "content_links").id != first
    assert drive.find_file(ROOT, "other name") is None


def test_typing_into_a_sheet_again_keeps_its_id(drive):
    made = drive.add_sheet(ROOT, "content_links", [SheetTab(name="a")])
    again = drive.add_sheet(ROOT, "content_links", [SheetTab(name="b")])

    assert again == made
    assert [tab.name for tab in drive.sheet(made).tabs] == ["b"]


def test_a_file_that_is_no_sheet_is_refused(drive):
    photo = drive.add_file(ROOT, "photo.jpg")

    with pytest.raises(DriveError, match="Export only supports"):
        drive.sheet(photo)


def test_read_live_mode_still_reads_sheets(drive):
    made = drive.add_sheet(ROOT, "content_links", [SheetTab(name="a")])
    guarded = ReadOnlyDrive(drive)

    assert guarded.find_file(ROOT, "content_links").id == made
    assert [tab.name for tab in guarded.sheet(made).tabs] == ["a"]


def _drive_answering(content: bytes) -> Drive:
    """A real `Drive` whose export call answers `content`: no network."""
    drive = Drive.__new__(Drive)
    drive._retries = 0
    drive._files = MagicMock()
    drive._files.export.return_value.execute.return_value = content
    return drive


def test_a_sheet_bigger_than_the_limit_is_refused_before_it_is_parsed(monkeypatch):
    book = Workbook()
    book.worksheets[0]["A1"] = LINK
    content = _xlsx(book)
    drive = _drive_answering(content)

    monkeypatch.setenv("OFM_GDRIVE_SHEET_MAX_BYTES", str(len(content)))
    assert drive.sheet("file-1").tabs[0].rows == [[LINK]]

    monkeypatch.setenv("OFM_GDRIVE_SHEET_MAX_BYTES", str(len(content) - 1))
    parsed = MagicMock()
    monkeypatch.setattr(drive_client, "read_xlsx", parsed)
    with pytest.raises(DriveError, match="too big to read"):
        drive.sheet("file-1")
    parsed.assert_not_called()


def test_an_export_that_is_no_xlsx_is_a_drive_error_not_a_crash():
    drive = _drive_answering(b"<html>sign in</html>")

    with pytest.raises(DriveError, match="could not be read"):
        drive.sheet("file-1")
