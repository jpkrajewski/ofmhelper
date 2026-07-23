import pytest

from ofmhelpers.web import jobs


@pytest.fixture(autouse=True)
def _isolated_jobs_store(monkeypatch, tmp_path):
    """web/jobs.py now persists every job to disk (see jobs.py) -- point it
    at a per-test temp file so the test suite never reads or writes the
    real uploads/jobs.json. JOBS itself (the in-memory dict) stays
    process-wide across tests, same as before this change; this only
    isolates the on-disk copy."""
    monkeypatch.setattr(jobs, "STORE_FILE", tmp_path / "jobs.json")
