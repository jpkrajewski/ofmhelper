"""
Covers file_manager.py's "kieai_out" root: AI-generation output (see
KieAIClient's out_dir) used to be invisible to the file manager entirely --
finding or removing a stale generation meant shelling into the server. Now
it's browsable/deletable like uploads/ and downloads/, and deleting through
it is what makes a job's gallery card disappear too (see
web/stores/jobs.py's list_jobs()/_prune_missing_files()).
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import sign_in_agency

from ofmhelpers.db.stores.jobs import create_job, list_jobs
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.web.main import app


@pytest.fixture
def client():
    c = TestClient(app)
    sign_in_agency(c)
    return c


@pytest.fixture(autouse=True)
def _isolated_kieai_root(monkeypatch, tmp_path):
    root = tmp_path / "kieai_out"
    root.mkdir()
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(root))
    return root


def test_kieai_out_is_a_browsable_root(client, _isolated_kieai_root):
    (_isolated_kieai_root / "gen.mp4").write_bytes(b"video")

    r = client.get("/file-manager/disk", params={"root": "kieai_out"})

    assert r.status_code == 200
    assert "gen.mp4" in r.text


def test_deleting_a_file_through_the_manager_removes_its_job_from_the_gallery(
    client, _isolated_kieai_root
):
    out_file = _isolated_kieai_root / "gen.mp4"
    out_file.write_bytes(b"video")

    job_id = create_job("seedance", {})
    run_job(job_id, lambda: [{"name": "gen.mp4", "path": str(out_file)}], {})
    assert any(j.id == job_id for j in list_jobs())

    r = client.post(
        "/file-manager/delete",
        data={"path": "gen.mp4", "root": "kieai_out"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    assert not any(j.id == job_id for j in list_jobs())
