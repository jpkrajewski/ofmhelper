"""
Unit tests for web/schemas.py -- the Pydantic contract at the persistence
boundary. Verifies the models accept exactly what the current code writes and
reject malformed payloads (the validation the raw-dict/JSON layer never had).
"""

import time

import pytest
from pydantic import ValidationError

from ofmhelpers.web.schemas import ApprovalToken, Job, JobStatus, Todo


def _job_dict(**overrides):
    base = {
        "id": "9a0c51ed",
        "task": "fake_ai",
        "params": {"prompt": "hi"},
        "actor": "admin",
        "status": "running",
        "result": None,
        "error": None,
        "created_at": time.time(),
    }
    base.update(overrides)
    return base


def test_job_validates_a_current_running_record():
    job = Job.model_validate(_job_dict())
    assert job.id == "9a0c51ed"
    assert job.status is JobStatus.running
    assert job.result is None


@pytest.mark.parametrize(
    "result",
    [
        [{"name": "a.mp4", "path": "/app/kieai_out/a.mp4"}],  # flat one-file-per-entry
        [{"url": "u", "success": True, "output_paths": ["/x"]}],  # grouped by url
        "1AbCdriveFileId",  # bare scalar (todo_drive_upload)
        None,
    ],
)
def test_job_accepts_every_result_shape_used_today(result):
    job = Job.model_validate(_job_dict(status="done", result=result))
    assert job.result == result


def test_job_preserves_optional_preview_payload():
    job = Job.model_validate(_job_dict(preview={"remote_url": "https://x/y.mp4"}))
    assert job.preview == {"remote_url": "https://x/y.mp4"}


def test_job_rejects_unknown_status_enum():
    with pytest.raises(ValidationError):
        Job.model_validate(_job_dict(status="queued"))


def test_job_rejects_missing_required_field():
    bad = _job_dict()
    del bad["created_at"]
    with pytest.raises(ValidationError):
        Job.model_validate(bad)


def test_job_rejects_wrong_type_for_created_at():
    with pytest.raises(ValidationError):
        Job.model_validate(_job_dict(created_at="not-a-number"))


def test_todo_validates_a_full_add_todo_record():
    todo = Todo.model_validate(
        {
            "id": "abc12345",
            "model_name": "Model A",
            "url": "https://replicate/x",
            "comments": "do it",
            "checked": False,
            "created_at": time.time(),
            "created_by": "admin",
            "asset_path": None,
            "asset_name": None,
            "approved": False,
            "rejected": False,
            "reject_comment": None,
            "drive_file_id": None,
            "drive_uploaded_at": None,
            "drive_upload_job_id": None,
        }
    )
    assert todo.model_name == "Model A"
    assert todo.approved is False


def test_todo_accepts_the_partial_shape_import_todos_writes():
    # import_todos omits every asset/approval field -- defaults must fill them.
    todo = Todo.model_validate(
        {
            "id": "def67890",
            "model_name": "Model B",
            "url": "https://replicate/y",
            "comments": "",
            "checked": False,
            "created_at": time.time(),
            "created_by": "admin",
        }
    )
    assert todo.asset_path is None
    assert todo.drive_upload_job_id is None


def test_todo_rejects_missing_required_field():
    with pytest.raises(ValidationError):
        Todo.model_validate({"id": "x", "created_at": time.time()})  # no model_name/url


def test_approval_token_round_trips_current_shape():
    now = time.time()
    tok = ApprovalToken.model_validate(
        {
            "token": "sometoken",
            "todo_id": "abc12345",
            "asset_path": "uploads/assets/x__ready.png",
            "created_at": now,
            "expires_at": now + 3600,
            "used_at": None,
        }
    )
    assert tok.used_at is None
    assert tok.expires_at > tok.created_at


def test_approval_token_rejects_missing_asset_path():
    now = time.time()
    with pytest.raises(ValidationError):
        ApprovalToken.model_validate(
            {"token": "t", "todo_id": "x", "created_at": now, "expires_at": now}
        )
