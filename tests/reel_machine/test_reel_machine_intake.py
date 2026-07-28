"""
Intake is now just "get the file, measure it". These cover the two branches
that aren't a subprocess call: a local upload is copied rather than
downloaded, and a failed Instagram download gets the /cookies hint appended.
"""

from pathlib import Path
from unittest import mock

import pytest

from ofmhelpers.reel_machine import intake


def test_local_file_is_copied_into_the_job_dir(tmp_path):
    src = tmp_path / "upload.mp4"
    src.write_bytes(b"video bytes")
    work_dir = tmp_path / "job"

    dest = intake.fetch_source(str(src), work_dir)

    assert dest == work_dir / "reference.mp4"
    assert dest.read_bytes() == b"video bytes"


def test_url_is_downloaded(tmp_path):
    downloaded = tmp_path / "reel.mp4"
    downloaded.write_bytes(b"x")
    with mock.patch.object(
        intake,
        "download",
        return_value=mock.Mock(success=True, output_paths=[downloaded]),
    ):
        assert intake.fetch_source("https://example.com/r", tmp_path) == downloaded


def test_failed_instagram_download_points_at_the_cookies_page(tmp_path):
    with (
        mock.patch.object(
            intake,
            "download",
            return_value=mock.Mock(success=False, output_paths=[], error="HTTP 400"),
        ),
        pytest.raises(RuntimeError, match="/cookies"),
    ):
        intake.fetch_source("https://instagram.com/reel/abc", tmp_path)


def test_run_intake_reports_the_probed_duration(tmp_path):
    video = tmp_path / "reference.mp4"
    video.write_bytes(b"x")
    with (
        mock.patch.object(intake, "fetch_source", return_value=video),
        mock.patch.object(intake, "probe_duration", return_value=11.5),
    ):
        result = intake.run_intake("https://example.com/r", tmp_path)

    assert result.video_path == video
    assert result.duration == 11.5
    assert result.source_url == "https://example.com/r"


def test_run_intake_records_no_source_url_for_a_local_upload(tmp_path):
    upload = tmp_path / "upload.mp4"
    upload.write_bytes(b"x")
    with (
        mock.patch.object(intake, "fetch_source", return_value=Path(upload)),
        mock.patch.object(intake, "probe_duration", return_value=5.0),
    ):
        assert intake.run_intake(str(upload), tmp_path).source_url is None
