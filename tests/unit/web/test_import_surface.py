# Copyright (C) 2026 jpkrajewski

import pickle

import pytest
from rq.utils import import_attribute

from ofmhelpers.db.jobs import store
from ofmhelpers.web.routers import task_helpers

TASK_HELPERS = (
    "assets_root",
    "uploads_root",
    "assets_root_for",
    "assets_root_for_actor",
    "assets_root_for_user",
    "in_asset_root",
    "download_name",
    "asset_card",
    "build_ordered_paths",
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


@pytest.mark.parametrize("name", TASK_HELPERS)
def test_task_helpers_package_re_exports(name):
    assert hasattr(task_helpers, name)


def test_task_helpers_all_matches_what_is_actually_exported():
    assert set(task_helpers.__all__) == set(TASK_HELPERS)


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
    "ofmhelpers.features.drive.uploads.upload_files": (
        "ofmhelpers.features.storage.jobs.upload_files"
    ),
    "ofmhelpers.features.drive.folders.sync": ("ofmhelpers.features.storage.jobs.sync"),
}


@pytest.mark.parametrize(("old", "new"), QUEUED_PATHS.items())
def test_a_run_function_pickled_under_its_router_path_is_the_features(old, new):
    module, name = old.rsplit(".", 1)
    stored = f"c{module}\n{name}\n.".encode()

    assert pickle.loads(stored) is import_attribute(new)  # noqa: S301


def test_run_job_still_answers_at_the_stores_path_rq_looks_up():
    old = import_attribute("ofmhelpers.db.stores.jobs.run_job")

    assert callable(old)
    assert import_attribute("ofmhelpers.features.jobs.runner.run_job") is not old


def test_the_stores_run_job_hands_the_job_to_the_registered_runner(monkeypatch):
    seen = []
    monkeypatch.setattr(store, "_runners", lambda: [lambda *args: seen.append(args)])

    store.run_job("j1", print, {"a": 1})

    assert seen == [("j1", print, {"a": 1})]


def test_the_stores_run_job_refuses_loudly_with_no_runner(monkeypatch):
    monkeypatch.setattr(store, "_runners", list)

    with pytest.raises(RuntimeError, match="no job runner registered"):
        store.run_job("j1", print, {})
