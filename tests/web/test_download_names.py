"""Downloaded images/videos are saved camera-roll style, not under the
generator's name: IMG_1234.png, VID_1234.mp4. Other files keep their name."""

import re

from ofmhelpers.web.routers.task_helpers import (
    asset_card,
    download_name,
    serve_job_file,
)
from ofmhelpers.web.schemas import Job


def test_images_and_videos_get_camera_roll_names():
    assert re.fullmatch(r"IMG_\d{4}\.png", download_name("seedream_ab12cd.png"))
    assert re.fullmatch(r"VID_\d{4}\.mp4", download_name("wan3_9f8e_capcut.mp4"))


def test_other_files_keep_their_name():
    assert download_name("copies.zip") == "copies.zip"
    assert download_name("voice.mp3") == "voice.mp3"


def test_prefixes_come_from_config(monkeypatch):
    monkeypatch.setenv("OFM_IMAGE_DOWNLOAD_PREFIX", "PIC")
    assert re.fullmatch(r"PIC\d{4}\.jpg", download_name("x.jpg"))


def test_asset_card_carries_the_download_name():
    card = asset_card("kie_result.jpg", 0, "/nanobanana/files/j1")
    assert card.name == "kie_result.jpg"  # still shown on the card
    assert re.fullmatch(r"IMG_\d{4}\.jpg", card.download_name)


def test_attachment_download_uses_the_camera_roll_name(tmp_path):
    out = tmp_path / "seedance_x1y2.mp4"
    out.write_bytes(b"mp4")
    job = Job(
        id="j1",
        task="seedance",
        created_at=0.0,
        status="done",
        result=[{"name": out.name, "path": str(out)}],
    )
    disposition = serve_job_file(job, 0).headers["content-disposition"]
    assert re.search(r'filename="VID_\d{4}\.mp4"', disposition)


def test_digit_count_comes_from_config(monkeypatch):
    monkeypatch.setenv("OFM_DOWNLOAD_NAME_DIGITS", "6")
    assert re.fullmatch(r"IMG_\d{6}\.png", download_name("x.png"))
