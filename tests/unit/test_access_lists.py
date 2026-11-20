from ofmhelpers.auth.access import (
    ACCESS,
    ADMIN,
    MANAGER,
    PAGES,
    RETIRED,
    USER,
    VA,
    Action,
    Area,
    allowed,
    areas_of,
)
from ofmhelpers.schemas.time_zones import TIME_ZONES, time_zone
from ofmhelpers.schemas.users import Role


def test_each_role_has_the_pages_of_the_one_below_and_its_own():
    assert set(VA) < set(USER) < set(ADMIN)
    assert set(ADMIN) == set(Area)
    assert set(PAGES) == set(Role)
    for pages in PAGES.values():
        assert len(pages) == len(set(pages))


def test_a_va_has_exactly_the_tools_they_work_in():
    assert VA == (
        Area.SESSION,
        Area.HOME,
        Area.JOBS,
        Area.REFS,
        Area.GENERATE,
        Area.CLONE,
        Area.DOWNLOAD,
        Area.EDIT,
        Area.HANDMADE,
    )


def test_the_map_is_read_off_the_lists_and_a_retired_page_is_nobodys():
    for role, pages in PAGES.items():
        assert set(areas_of(role)) == set(pages) - RETIRED
    for area in RETIRED:
        assert ACCESS[area] == frozenset()
    assert ACCESS[Area.CLONE] == frozenset(Role)
    assert ACCESS[Area.VAS] == frozenset({Role.USER, Role.ADMIN, Role.MANAGER})
    assert ACCESS[Area.USERS] == frozenset({Role.ADMIN})


def test_a_manager_sees_a_users_pages_and_the_chats_and_does_nothing():
    assert set(MANAGER) == (set(USER) - {Area.ACCOUNT, Area.COOKIES}) | {Area.CHATTING}
    assert Area.USERS not in MANAGER
    assert not any(allowed(Role.MANAGER, action) for action in Action)


def test_only_a_va_uploads_handmade_reels():
    assert allowed(Role.VA, Action.UPLOAD_HANDMADE)
    assert not allowed(Role.USER, Action.UPLOAD_HANDMADE)
    assert not allowed(Role.ADMIN, Action.UPLOAD_HANDMADE)


def test_a_va_hides_nothing():
    assert not allowed(Role.VA, Action.HIDE)
    assert allowed(Role.USER, Action.HIDE)
    assert allowed(Role.ADMIN, Action.HIDE)


def test_the_globe_offers_the_teams_zones():
    assert [zone.label for zone in TIME_ZONES] == ["Warsaw", "Ambala", "Manila"]
    assert time_zone("Asia/Manila").label == "Manila"


def test_any_real_zone_is_taken_and_anything_else_is_the_first():
    york = time_zone("America/New_York")

    assert (york.zone, york.label) == ("America/New_York", "New York")
    assert time_zone("Asia/Kolkata").label == "Ambala"
    for nothing in (None, "", "Mars/Olympus", "../../etc/passwd", "<script>"):
        assert time_zone(nothing) == TIME_ZONES[0]
