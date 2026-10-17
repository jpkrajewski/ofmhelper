"""job_assets / serve_job_file: one result-to-files mapping for both result
shapes (one entry per file, or grouped per source URL), so a card's file index
always resolves to the file it shows."""

import re

import pytest
from fastapi import HTTPException

from ofmhelpers.web.routers.task_helpers import job_assets, serve_job_file
from ofmhelpers.web.schemas import Job


def _grouped_job(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.jpg"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    return Job(
        id="j1",
        task="download_images",
        created_at=0.0,
        status="done",
        result=[
            {"url": "u1", "success": True, "output_paths": [str(a)]},
            {"url": "u2", "success": False, "error": "404", "output_paths": []},
            {"url": "u3", "success": True, "output_paths": [str(b)]},
        ],
    )


def test_grouped_result_numbers_files_across_groups(tmp_path):
    assets, failed = job_assets(_grouped_job(tmp_path), "/dl/files")
    assert [(a.name, a.source) for a in assets] == [
        ("a.mp4", "u1"),
        ("b.jpg", "u3"),
    ]
    assert assets[1].view_url == "/dl/files/j1/1"
    assert [f.model_dump() for f in failed] == [{"source": "u2", "error": "404"}]


def test_grouped_file_index_serves_the_same_file(tmp_path):
    job = _grouped_job(tmp_path)
    assert str(serve_job_file(job, 1, as_attachment=False).path).endswith("b.jpg")
    disposition = serve_job_file(job, 0).headers["content-disposition"]
    assert re.search(r'filename="VID_\d{4}\.mp4"', disposition)
    with pytest.raises(HTTPException):
        serve_job_file(job, 2)


def test_flat_result_keeps_the_remote_url():
    job = Job(
        id="j2",
        task="nanobanana",
        created_at=0.0,
        status="done",
        result=[{"name": "x.png", "path": None, "remote_url": "https://r/x.png"}],
    )
    [asset], failed = job_assets(job, "/nb/files")
    assert asset.view_url == "https://r/x.png"
    assert failed == []


def test_unfinished_job_has_no_assets():
    assert job_assets(Job(id="j3", task="wan3", created_at=0.0), "/x/files") == ([], [])
