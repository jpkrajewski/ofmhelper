"""
Migration-correctness test for scripts/backfill_state.py: every record in the
legacy JSON files lands in Postgres exactly once, and re-running is idempotent
(no duplicates) -- the guarantee that lets the one-time migration be re-run
safely.
"""

import json

from ofmhelpers.web.db.backfill import backfill_dir
from ofmhelpers.web.db.repository import JobRepository
from ofmhelpers.web.jobs import get_job
from ofmhelpers.web import todos, approval_tokens


def _write_fixtures(base):
    (base / "jobs.json").write_text(
        json.dumps(
            {
                "job0001": {
                    "id": "job0001",
                    "task": "seedance",
                    "params": {"prompt": "a cat"},
                    "actor": "admin",
                    "status": "done",
                    "result": [{"name": "a.mp4", "path": "/app/kieai_out/a.mp4"}],
                    "error": None,
                    "created_at": 1000.0,
                    # a render-time extra that must be ignored, not break import
                    "status_url": "/seedance/jobs/job0001",
                },
                "job0002": {
                    "id": "job0002",
                    "task": "todo_drive_upload",
                    "params": {"todo_id": "t1"},
                    "actor": "va",
                    "status": "done",
                    "result": "1DriveFileId",
                    "error": None,
                    "created_at": 1001.0,
                },
            }
        )
    )
    (base / "todos.json").write_text(
        json.dumps(
            [
                {
                    "id": "todo0001",
                    "model_name": "Model A",
                    "url": "https://replicate/x",
                    "comments": "do it",
                    "checked": False,
                    "created_at": 2000.0,
                    "created_by": "admin",
                },
            ]
        )
    )
    (base / "approval_tokens.json").write_text(
        json.dumps(
            [
                {
                    "token": "tok0001",
                    "todo_id": "todo0001",
                    "asset_path": "uploads/assets/x__ready.png",
                    "created_at": 2500.0,
                    "expires_at": 9_999_999_999.0,
                    "used_at": None,
                },
            ]
        )
    )


def test_backfill_imports_every_record_once(tmp_path):
    _write_fixtures(tmp_path)

    counts = backfill_dir(tmp_path, archive=False)
    assert counts == {"jobs": 2, "todos": 1, "approval_tokens": 1}

    # Jobs landed with their exact shapes (including the bare-string result).
    assert get_job("job0001")["result"] == [
        {"name": "a.mp4", "path": "/app/kieai_out/a.mp4"}
    ]
    assert get_job("job0002")["result"] == "1DriveFileId"
    assert get_job("job0001").get("status_url") is None  # extra was ignored

    assert todos.get_todo("todo0001")["model_name"] == "Model A"
    assert approval_tokens.get_token("tok0001")["todo_id"] == "todo0001"


def test_backfill_is_idempotent_no_duplicates(tmp_path):
    _write_fixtures(tmp_path)

    backfill_dir(tmp_path, archive=False)
    backfill_dir(tmp_path, archive=False)  # run again against the same files

    # Still exactly two jobs / one todo / one token -- merge-by-PK, no dupes.
    # (Count via the repository, not list_jobs(), which would self-heal away
    # the jobs whose fixture result files don't exist on this disk.)
    assert len(JobRepository().list_all()) == 2
    assert len(todos.list_todos()) == 1
    assert approval_tokens.get_token("tok0001") is not None


def test_backfill_archives_source_files(tmp_path):
    _write_fixtures(tmp_path)

    backfill_dir(tmp_path, archive=True)

    # Originals moved out of the way, not deleted.
    assert not (tmp_path / "jobs.json").exists()
    archived = list((tmp_path / "archive").glob("jobs.json.*"))
    assert len(archived) == 1
