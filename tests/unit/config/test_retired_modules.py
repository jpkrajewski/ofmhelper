import fnmatch

from ofmhelpers.auth.access import ACCESS, RETIRED, Area
from ofmhelpers.config.retired import retired
from ofmhelpers.web.routers import ROUTERS
from ofmhelpers.web.routers.downloads import reels
from ofmhelpers.web.routers.generation import clone_links
from ofmhelpers.web.routers.image_tools import pictures
from ofmhelpers.web.routers.video_tools import repurpose


def test_every_retired_area_is_a_real_area_nobody_opens():
    assert {Area(name) for name in retired().areas} == RETIRED
    assert all(ACCESS[area] == frozenset() for area in RETIRED)


def test_a_retired_module_serves_no_route():
    assert retired().routers == {"clone_links", "pictures", "reels"}
    for module in (clone_links, pictures, reels):
        assert module.router not in ROUTERS


def test_a_module_not_in_the_map_is_served():
    assert repurpose.router in ROUTERS
    assert Area.REPURPOSE not in RETIRED


def test_retired_test_globs_match_the_parked_files():
    globs = retired().tests
    assert any(fnmatch.fnmatch("tests/unit/test_reels_page.py", g) for g in globs)
    assert not any(
        fnmatch.fnmatch("tests/integration/web/test_repurpose_run.py", g) for g in globs
    )
