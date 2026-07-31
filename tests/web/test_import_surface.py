"""Three modules were split into packages whose `__init__` re-exports the old
names: `db/repositories`, `routers/task_helpers`, `routers/generation/replicate`.

The re-export is the whole point -- ~13 routers and every store import through
it, and they were deliberately left untouched by the split. A name that quietly
stops being re-exported breaks them at import time, in a way no single feature
test would attribute to the split.
"""

import pytest

from ofmhelpers.web.db import repositories
from ofmhelpers.web.routers import task_helpers
from ofmhelpers.web.routers.generation import replicate

REPOSITORIES = (
    "ApprovalTokenRepository",
    "InstagramStatsRepository",
    "JobRepository",
    "ModelRepository",
    "TodoRepository",
)

TASK_HELPERS = (
    "ASSETS_ROOT",
    "AUDIO_EXTS",
    "IMAGE_EXTS",
    "UPLOADS_ROOT",
    "VIDEO_EXTS",
    "IMAGE_KINDS",
    "IMAGE_VIDEO_KINDS",
    "MEDIA_KINDS",
    "asset_card",
    "build_ordered_paths",
    "classify_kind",
    "flatten_grouped_results",
    "grouped_job_status_payload",
    "job_inputs",
    "job_status_payload",
    "make_job_dir",
    "media_response",
    "reference_asset",
    "register_generated_asset",
    "register_grouped_results",
    "require_upload_kind",
    "resolve_existing_ref",
    "resolve_reference_uploads",
    "safe_filename",
    "save_asset",
    "save_upload",
    "serve_job_file",
    "strip_asset_hash_prefix",
)


@pytest.mark.parametrize("name", REPOSITORIES)
def test_repositories_package_re_exports(name):
    assert hasattr(repositories, name)


@pytest.mark.parametrize("name", TASK_HELPERS)
def test_task_helpers_package_re_exports(name):
    assert hasattr(task_helpers, name)


def test_task_helpers_all_matches_what_is_actually_exported():
    """`__all__` is what `from task_helpers import *` and the docs promise."""
    assert set(task_helpers.__all__) == set(TASK_HELPERS)


def test_replicate_router_still_owns_its_url_prefix():
    """The split moved code between files; it must not have moved a URL."""
    assert replicate.router.prefix == "/replicate"
    paths = {route.path for route in replicate.router.routes}
    assert "/replicate/intake" in paths
    assert "/replicate/generate" in paths
    assert "/replicate/jobs/{job_id}" in paths
