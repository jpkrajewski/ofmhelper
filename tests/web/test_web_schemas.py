"""
Unit tests for web/schemas.py -- the Pydantic contract at the persistence
boundary. Verifies the models accept exactly what the current code writes and
reject malformed payloads (the validation the raw-dict/JSON layer never had).
"""

import time

import pytest
from pydantic import ValidationError

from ofmhelpers.web.schemas import Job, JobStatus


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
