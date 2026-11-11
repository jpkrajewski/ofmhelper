"""Two modules were split into packages whose `__init__` re-exports the old
names: `db/repositories` and `routers/task_helpers`.

The re-export is the whole point -- ~13 routers and every store import through
it, and they were deliberately left untouched by the split. A name that quietly
stops being re-exported breaks them at import time, in a way no single feature
test would attribute to the split.
"""

import importlib
import pickle

import pytest

from ofmhelpers.db import repositories
from ofmhelpers.web.routers import task_helpers

REPOSITORIES = ("ApplicationRepository", "JobRepository")

TASK_HELPERS = (
    "assets_root",
    "AUDIO_EXTS",
    "IMAGE_EXTS",
    "uploads_root",
    "VIDEO_EXTS",
    "VIDEO_KINDS",
    "assets_root_for",
    "assets_root_for_actor",
    "assets_root_for_user",
    "in_asset_root",
    "IMAGE_KINDS",
    "MEDIA_KINDS",
    "convert_heic_upload",
    "download_name",
    "asset_card",
    "build_ordered_paths",
    "classify_kind",
    "job_assets",
    "add_job_routes",
    "job_inputs",
    "job_status_payload",
    "keep_files",
    "keep_videos",
    "make_job_dir",
    "media_response",
    "reference_asset",
    "register_generated_asset",
    "register_grouped_results",
    "require_upload_kind",
    "resolve_existing_ref",
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


# Jobs waiting in Redis across a deploy name their functions by the path they
# had when queued: RQ looks `run_job` up by its dotted name, and unpickles the
# run function it was handed by module + name. Both moved to `features/`
# (2026-11-11); every old path must keep answering with the feature's function.
QUEUED_PATHS = {
    "ofmhelpers.web.routers.generation.ai.run_ai": (
        "ofmhelpers.features.generate.service.run_ai"
    ),
    "ofmhelpers.web.routers.generation.clone._run_clone": (
        "ofmhelpers.features.clone.run.run_clone"
    ),
    "ofmhelpers.web.routers.generation.clone_links.run_clone_link": (
        "ofmhelpers.features.clone_links.job.run_clone_link"
    ),
    "ofmhelpers.web.routers.image_tools.pictures.run_picture": (
        "ofmhelpers.features.pictures.service.run_picture"
    ),
    "ofmhelpers.web.routers.downloads.viral.run_viral": (
        "ofmhelpers.features.viral.page.run_viral"
    ),
    "ofmhelpers.web.routers.video_tools.uniqify._run_uniqify": (
        "ofmhelpers.features.uniqify.service.run_uniqify"
    ),
    "ofmhelpers.web.routers.video_tools.edit.run_cut": (
        "ofmhelpers.features.edit.service.run_cut"
    ),
    "ofmhelpers.web.routers.video_tools.edit.run_prepare": (
        "ofmhelpers.features.edit.service.run_prepare"
    ),
}


def _named(path: str) -> object:
    module, name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module), name)


@pytest.mark.parametrize(("old", "new"), QUEUED_PATHS.items())
def test_a_run_function_pickled_under_its_router_path_is_the_features(old, new):
    module, name = old.rsplit(".", 1)
    # What RQ stored for a job queued before the move: a reference by name.
    stored = f"c{module}\n{name}\n.".encode()

    assert pickle.loads(stored) is _named(new)  # noqa: S301 -- built right here


def test_run_job_still_answers_at_the_stores_path_rq_looks_up():
    from rq.utils import import_attribute

    old = import_attribute("ofmhelpers.db.stores.jobs.run_job")

    assert callable(old)
    assert _named("ofmhelpers.features.jobs.runner.run_job") is not old


def test_the_stores_run_job_hands_the_job_to_the_registered_runner(monkeypatch):
    from ofmhelpers.db.stores import jobs as store

    seen = []
    monkeypatch.setattr(store, "_runners", lambda: [lambda *args: seen.append(args)])

    store.run_job("j1", print, {"a": 1})

    assert seen == [("j1", print, {"a": 1})]


def test_the_stores_run_job_refuses_loudly_with_no_runner(monkeypatch):
    from ofmhelpers.db.stores import jobs as store

    monkeypatch.setattr(store, "_runners", list)

    with pytest.raises(RuntimeError, match="no job runner registered"):
        store.run_job("j1", print, {})
