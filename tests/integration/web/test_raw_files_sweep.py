# Copyright (C) 2026 jpkrajewski

import os
import time
lazy from pathlib import Path

import pytest

from ofmhelpers.db.jobs.store import create_job, fail_job
from ofmhelpers.features.jobs import raw_files
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.integrations.ai_providers import tasks

DAY = 86400


@pytest.fixture(autouse=True)
def out(tmp_path, monkeypatch) -> Path:
    folder = tmp_path / "kieai_out"
    folder.mkdir()
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(folder))
    return folder


def _file(folder: Path, name: str, age_days: float = 15) -> Path:
    path = folder / name
    path.write_bytes(b"v")
    then = time.time() - age_days * DAY
    os.utime(path, (then, then))
    return path


def test_old_raw_downloads_nobody_names_go_and_the_rest_stays(out):
    raw = _file(out, "higgsfield_task-a.mp4")
    hashed = _file(out, "6bf5d71a152d1431fee239e8eb0bf916.mp4")
    export = _file(out, "higgsfield_task-a_capcut.mp4")
    done = create_job("clone", {"prompt": "p"})
    run_job(done, lambda: [{"name": export.name, "path": str(export)}], {})
    image = _file(out, "kie_task-b.png")
    failed = create_job("generate", {"prompt": "p"})
    fail_job(failed, "nope")
    _file(out, "unused.txt")
    log = _file(out, "tasks.jsonl")
    recent = _file(out, "kie_task-c.mp4", age_days=13)
    resumable = _file(out, "apiyi_task-d.mp4")
    tasks.remember(
        tasks.ProviderTask(
            job_id=failed,
            provider="apiyi",
            model="m",
            task_id="task-d",
            api_key="k",
        ),
    )
    create_job("clone", {"source": str(image)})
    (out / "apiyi").mkdir()
    in_folder = _file(out / "apiyi", "old.mp4")

    raw_files.remove_old()

    assert not raw.exists()
    assert not hashed.exists()
    assert export.exists()
    assert image.exists()
    assert log.exists()
    assert recent.exists()
    assert resumable.exists()
    assert in_folder.exists()
    assert (out / "unused.txt").exists()


def test_a_missing_folder_is_nothing_to_sweep(out, monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(out / "never-made"))

    assert raw_files.stale(time.time()) == []
