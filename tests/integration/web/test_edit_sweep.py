"""The /edit sweep (features/edit/cleanup.py): what /edit made is deleted a
day later -- uploads, cuts, light copies, strips, a killed cut's work folder
-- unless a job still names it: a job of another tool (a clone of a cut) for
as long as it is there, /edit's own cut or light copy while it runs or waits.
No ffmpeg: the files are stand-ins, so this runs in the default suite."""

import os
import time
from pathlib import Path

import pytest

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job, fail_job, get_job
from ofmhelpers.features.assets import store as task_helper_uploads
from ofmhelpers.features.edit import cleanup
from ofmhelpers.features.edit import service as edit_service
from ofmhelpers.features.jobs.runner import run_job

DAY = 86400


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(task_helper_uploads, "assets_root", lambda: root)
    # Light copies, strips and work folders of this test only.
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    return root


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def _old_files(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for name in names:
        path = folder / f"{name}.mp4"
        path.write_bytes(b"v")
        _age(path, 2 * DAY)
        made.append(path)
    return made


def test_the_sweep_deletes_what_edit_made_a_day_ago_unless_a_job_needs_it(store):
    staging = store / "users" / "7" / ".edit"
    old, needed = _old_files(staging, "old", "needed")
    fresh = staging / "fresh.mp4"
    fresh.write_bytes(b"v")
    create_job("clone", {"reference_videos": [str(needed)]})
    strips = Path(settings.infra.uploads_root) / ".previews" / "strips"
    strips.mkdir(parents=True, exist_ok=True)  # the strips made before .previews/edit
    old_strip = strips / "s.jpg"
    old_strip.write_bytes(b"j")
    _age(old_strip, 2 * DAY)
    left = Path(settings.infra.uploads_root) / ".edit" / "tmpabc"
    left.mkdir(parents=True, exist_ok=True)
    _age(left, 3600)

    cleanup.remove_old()

    assert not old.exists()
    assert fresh.exists()
    assert needed.exists()
    assert not old_strip.exists()
    assert not left.exists()


@pytest.mark.parametrize("part", ["users/7", "."], ids=["a person's", "the agency's"])
def test_a_cut_a_finished_clone_used_stays_for_its_recreate(store, part):
    """Prod 2026-11-07: a clone made from a cut the day before (job 45963883,
    in the agency's `.edit/`) lost the cut to this sweep -- kept only while
    the clone ran -- and its Recreate said "..._cut.mp4 is not on this
    server". A job of another tool holds what it names, done or failed."""
    cut, failed_cut = _old_files(store / part / ".edit", "cut", "failed_cut")
    done = create_job("clone", {"reference_videos": [str(cut)]})
    run_job(done, list, {})
    failed = create_job("clone", {"reference_videos": [str(failed_cut)]})
    fail_job(failed, "No credits")

    cleanup.remove_old()

    assert get_job(done).status == "done"
    assert cut.exists()
    assert failed_cut.exists()


def test_an_upload_stays_while_its_cut_waits_and_goes_once_it_is_done(store):
    """/edit's own jobs name the upload they read: a cut or light copy still
    in the edit line keeps it (review 2026-11-07: an upload a day old was
    deleted under its queued cut); done, they keep nothing."""
    waiting, cut_done, prepared = _old_files(
        store / "users" / "7" / ".edit", "waiting", "cut_done", "prepared"
    )
    create_job("edit_cut", {"video": str(waiting), "start": 0, "end": 2})
    finished = create_job("edit_cut", {"video": str(cut_done), "start": 0, "end": 2})
    run_job(finished, list, {})
    made = create_job("edit_prepare", {"video": str(prepared)})
    run_job(made, list, {})

    cleanup.remove_old()

    assert waiting.exists()
    assert not cut_done.exists()
    assert not prepared.exists()


def test_the_sweep_deletes_light_copies_a_day_old(store):
    folder = edit_service.prepared_dir()
    folder.mkdir(parents=True, exist_ok=True)
    old, fresh = folder / "old_540p.mp4", folder / "fresh_frames.json"
    for path in (old, fresh):
        path.write_bytes(b"x")
    _age(old, 2 * DAY)

    cleanup.remove_old()

    assert not old.exists()
    assert fresh.exists()
