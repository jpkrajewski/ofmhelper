"""
recover_orphaned_jobs backfills a 'done' job row for any kieai_out/ file that
no existing job's result references -- the gap this closes: a job whose
completion write never landed in Postgres (crash/backfill miss) even though
the generated file survived on disk.
"""

import json

from ofmhelpers.web.db.recover_orphaned_jobs import recover_orphaned_jobs
from ofmhelpers.web.db.repository import JobRepository
from ofmhelpers.web.jobs import get_job


def _write_task_log(out_dir, entries):
    (out_dir / "tasks.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n"
    )


def test_recovers_file_with_logged_prompt(tmp_path):
    out_dir = tmp_path / "kieai_out"
    out_dir.mkdir()
    (out_dir / "abc123.png").write_bytes(b"fake png")
    _write_task_log(
        out_dir,
        [
            {
                "taskId": "abc123",
                "model": "nano-banana-pro",
                "prompt": "a cat",
                "createdAt": 1000.0,
            }
        ],
    )

    count = recover_orphaned_jobs(out_dir=out_dir, task_log=out_dir / "tasks.jsonl")
    assert count == 1

    jobs = [j for j in JobRepository().list_all() if j["task"] == "nanobanana"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["status"] == "done"
    assert job["params"]["prompt"] == "a cat"
    assert job["result"] == [
        {"name": "abc123.png", "path": str(out_dir / "abc123.png")}
    ]


def test_recovers_file_with_no_task_log_entry(tmp_path):
    out_dir = tmp_path / "kieai_out"
    out_dir.mkdir()
    (out_dir / "orphan.mp4").write_bytes(b"fake mp4")

    count = recover_orphaned_jobs(out_dir=out_dir, task_log=out_dir / "tasks.jsonl")
    assert count == 1

    jobs = [j for j in JobRepository().list_all() if j["task"] == "seedance"]
    assert len(jobs) == 1
    assert "unknown" in jobs[0]["params"]["prompt"]


def test_skips_file_already_referenced_by_a_job(tmp_path):
    out_dir = tmp_path / "kieai_out"
    out_dir.mkdir()
    (out_dir / "already.png").write_bytes(b"fake png")

    job_id = JobRepository().create("nanobanana", {"prompt": "existing"}, actor="admin")
    JobRepository().update_status(
        job_id,
        "done",
        result=[{"name": "already.png", "path": str(out_dir / "already.png")}],
    )

    count = recover_orphaned_jobs(out_dir=out_dir, task_log=out_dir / "tasks.jsonl")
    assert count == 0
    assert get_job(job_id)["params"]["prompt"] == "existing"


def test_idempotent_second_run_finds_nothing(tmp_path):
    out_dir = tmp_path / "kieai_out"
    out_dir.mkdir()
    (out_dir / "abc123.png").write_bytes(b"fake png")
    _write_task_log(
        out_dir,
        [
            {
                "taskId": "abc123",
                "model": "nano-banana-pro",
                "prompt": "a cat",
                "createdAt": 1000.0,
            }
        ],
    )

    first = recover_orphaned_jobs(out_dir=out_dir, task_log=out_dir / "tasks.jsonl")
    second = recover_orphaned_jobs(out_dir=out_dir, task_log=out_dir / "tasks.jsonl")
    assert first == 1
    assert second == 0


def test_ignores_non_asset_files(tmp_path):
    out_dir = tmp_path / "kieai_out"
    out_dir.mkdir()
    (out_dir / "tasks.jsonl").write_text("")
    (out_dir / "resolved.jsonl").write_text("")
    (out_dir / "completions.jsonl").write_text("")

    count = recover_orphaned_jobs(out_dir=out_dir, task_log=out_dir / "tasks.jsonl")
    assert count == 0
