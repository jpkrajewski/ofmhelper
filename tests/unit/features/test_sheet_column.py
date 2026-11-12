"""features/downloads/sheet.py `column_of`: a day's links are found by the
date on top of their column, in whichever tab and column it stands."""

from datetime import date

from ofmhelpers.features.downloads.sheet import Column, column_of
from ofmhelpers.integrations.gdrive.models import DriveSheet, SheetTab

TODAY = date(2026, 11, 12)
TOMORROW = date(2026, 11, 13)
A = "https://www.instagram.com/reel/aaa/?cplk=one"
B = "https://www.instagram.com/reel/bbb/"
C = "https://www.tiktok.com/@user/video/123"


def _sheet(*tabs: SheetTab) -> DriveSheet:
    return DriveSheet(tabs=list(tabs))


def test_the_links_under_todays_date_top_to_bottom_and_no_other_days():
    tab = SheetTab(name="Sheet1", rows=[[TODAY, TOMORROW], [A, C], [B, None]])

    assert column_of(_sheet(tab), TODAY) == Column(links=[A, B])
    assert column_of(_sheet(tab), TOMORROW) == Column(links=[C])


def test_the_column_is_found_wherever_it_stands():
    """Not the first column, not the first tab, rows of different lengths."""
    notes = SheetTab(name="Notes", rows=[["MODEL", "LINK"], ["Jaxson", C]])
    days = SheetTab(
        name="Days",
        rows=[["who", TOMORROW, TODAY], ["ann", None, A], ["bob"], [None, C, B]],
    )

    assert column_of(_sheet(notes, days), TODAY) == Column(links=[A, B])


def test_no_column_for_the_day_is_none_not_an_empty_day():
    tab = SheetTab(name="Sheet1", rows=[[TOMORROW], [A]])

    assert column_of(_sheet(tab), TODAY) is None
    assert column_of(_sheet(SheetTab(name="Empty")), TODAY) is None
    assert column_of(_sheet(), TODAY) is None
    # The date there, nothing under it yet: a day with no links.
    assert column_of(_sheet(SheetTab(name="s", rows=[[TODAY]])), TODAY) == Column(
        links=[]
    )


def test_a_date_below_the_first_row_is_not_a_column():
    tab = SheetTab(name="Sheet1", rows=[["links"], [TODAY], [A]])

    assert column_of(_sheet(tab), TODAY) is None


def test_a_link_counts_once_and_a_cell_may_hold_several():
    tab = SheetTab(name="Sheet1", rows=[[TODAY], [A], [f"{B}\n{A}\n\n{C}"], [B]])

    assert column_of(_sheet(tab), TODAY) == Column(links=[A, B, C])


def test_a_cell_that_is_no_link_is_skipped_and_counted_and_the_rest_still_come():
    tab = SheetTab(
        name="Sheet1",
        rows=[[TODAY], ["posted already"], [A], [TOMORROW], [f"{B}\nnot a link"], [C]],
    )

    assert column_of(_sheet(tab), TODAY) == Column(links=[A, C], skipped=3)


def test_a_link_without_its_scheme_gets_it():
    tab = SheetTab(name="Sheet1", rows=[[TODAY], ["instagram.com/reel/aaa/"]])

    assert column_of(_sheet(tab), TODAY) == Column(
        links=["https://instagram.com/reel/aaa/"]
    )


def test_the_same_day_in_two_tabs_gives_both_columns():
    one = SheetTab(name="one", rows=[[TODAY], [A]])
    two = SheetTab(name="two", rows=[[TODAY], [B], [A]])

    assert column_of(_sheet(one, two), TODAY) == Column(links=[A, B])
