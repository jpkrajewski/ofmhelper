"""The web layer's typed shapes: stored results keep their exact old JSON
shape, and the task registry covers every registered tool router."""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret")

from ofmhelpers.web.routers import ROUTERS
from ofmhelpers.web.routers.registry import DOWNLOAD_TOOLS, GENERATE_TOOLS, TOOLS
from ofmhelpers.web.schemas import ResultFile


def test_result_file_stores_only_the_fields_that_were_set():
    assert ResultFile(name="a.png", path="/x/a.png").to_result() == {
        "name": "a.png",
        "path": "/x/a.png",
    }
    # A remote-only kie.ai result keeps its explicit None path.
    assert ResultFile(
        name="v.mp4", path=None, remote_url="https://r/v"
    ).to_result() == {
        "name": "v.mp4",
        "path": None,
        "remote_url": "https://r/v",
    }


def test_every_registered_tool_is_a_mounted_router():
    mounted = {id(r) for r in ROUTERS}
    assert all(id(tool.router) in mounted for tool in TOOLS.values())
    assert TOOLS["wan3"].files_prefix == "/wan3/files"


def test_gallery_subsets_are_in_the_registry():
    assert set(GENERATE_TOOLS) | set(DOWNLOAD_TOOLS) <= set(TOOLS)
    assert {"clone", "character_sheet", "uniqify"} <= set(TOOLS)
