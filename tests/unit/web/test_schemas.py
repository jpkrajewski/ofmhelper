# Copyright (C) 2026 jpkrajewski

from ofmhelpers.db.jobs.schema import ResultFile
from ofmhelpers.db.jobs.store import _stored
from ofmhelpers.web.routers import ROUTERS
from ofmhelpers.web.routers.generation import clone_links
from ofmhelpers.web.routers.image_tools import pictures
from ofmhelpers.web.routers.registry import DOWNLOAD_TOOLS, GENERATE_TOOLS, TOOLS


def test_result_file_stores_only_the_fields_that_were_set():
    assert _stored([ResultFile(name="a.png", path="/x/a.png")])[0] == {
        "name": "a.png",
        "path": "/x/a.png",
    }
    remote = ResultFile(name="v.mp4", path=None, remote_url="https://r/v")
    assert _stored([remote])[0] == {
        "name": "v.mp4",
        "path": None,
        "remote_url": "https://r/v",
    }


def test_every_registered_tool_is_a_mounted_router():
    served = {id(r) for r in ROUTERS}
    retired_routers = {id(clone_links.router), id(pictures.router)}
    assert all(id(t.router) in served | retired_routers for t in TOOLS.values())
    assert TOOLS["wan3"].files_prefix == "/wan3/files"


def test_gallery_subsets_are_in_the_registry():
    assert set(GENERATE_TOOLS) | set(DOWNLOAD_TOOLS) <= set(TOOLS)
    assert {"clone", "character_sheet", "uniqify"} <= set(TOOLS)
