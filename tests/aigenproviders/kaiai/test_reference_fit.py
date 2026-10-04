"""Reference images over kie.ai's limits are shrunk before upload.

An oversized reference used to upload fine and then fail createTask with a
422 "reference_image_urls file size exceeds limit" (a 40MB, 12000px-wide
character sheet, in practice)."""

import os
from unittest import mock

import pytest
from PIL import Image

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.utils.image_fit import fit_image, fits


@pytest.fixture
def client(tmp_path):
    return KieAIClient(
        api_key="test-key",
        out_dir=tmp_path / "out",
        task_log=tmp_path / "tasks.jsonl",
        completions_log=tmp_path / "completions.jsonl",
        resolved_log=tmp_path / "resolved.jsonl",
    )


def noisy_png(path, size):
    """Random pixels, so the PNG is big and doesn't compress away."""
    Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(path)
    return path


def test_fit_image_respects_both_limits(tmp_path):
    src = noisy_png(tmp_path / "sheet.png", (1200, 300))
    max_bytes = 100_000

    out = fit_image(src, tmp_path / "out.jpg", max_bytes, 1000)

    assert out.stat().st_size <= max_bytes
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert max(img.size) <= 1000


def test_small_image_fits_untouched(tmp_path):
    src = noisy_png(tmp_path / "ok.png", (10, 10))
    assert fits(src, 10_000_000, 8000)


def test_upload_sends_a_fitted_copy_and_keeps_the_original(
    client, tmp_path, monkeypatch
):
    monkeypatch.setenv("OFM_KIEAI_REF_IMAGE_MAX_SIDE", "500")
    src = noisy_png(tmp_path / "sheet.png", (2000, 400))
    original = src.read_bytes()

    with mock.patch("ofmhelpers.aigenproviders.kaiai.client.requests") as mreq:
        mreq.post.return_value.json.return_value = {
            "success": True,
            "data": {"downloadUrl": "https://tempfile.example/sheet.jpg"},
        }
        client.upload_local_file(str(src))

    sent = mreq.post.call_args.kwargs
    assert sent["data"]["fileName"] == "sheet.jpg"
    assert src.read_bytes() == original
    with Image.open(tmp_path / "out" / "fitted" / "sheet.jpg") as img:
        assert img.size == (500, 100)
