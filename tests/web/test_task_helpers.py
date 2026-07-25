"""
Covers the "don't store a duplicate file" logic in web/routers/task_helpers.py:
- save_asset() content-hashes uploads into the shared assets store, so the
  same file uploaded twice (even under a different name, even by a different
  tool) is only ever written to disk once.
- build_ordered_paths() is what every reference-upload router
  (seedance/kling3/nanobanana) relies on to let a VA reuse a
  previously-uploaded reference, via save_asset for new uploads and
  resolve_existing_ref for explicitly-reused ones.
"""

import io
import json

import pytest
from fastapi import HTTPException, UploadFile

from ofmhelpers.web.routers.task_helpers import (
    asset_card,
    build_ordered_paths,
    register_generated_asset,
    resolve_existing_ref,
    save_asset,
    save_upload,
    serve_job_file,
)


def make_upload(name: str, content: bytes = b"hello") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=name)


def test_save_upload_writes_exactly_one_file(tmp_path):
    upload = make_upload("a.png")
    dest = save_upload(tmp_path, upload)

    assert dest == str(tmp_path / "a.png")
    assert list(tmp_path.iterdir()) == [tmp_path / "a.png"]


def test_save_asset_writes_exactly_one_file_named_after_the_hash(tmp_path):
    assets_root = tmp_path / "assets"
    upload = make_upload("a.png", b"some bytes")

    path = save_asset(upload, assets_root)

    files = list(assets_root.iterdir())
    assert len(files) == 1
    assert str(files[0]) == path
    assert path.endswith("__a.png")


def test_save_asset_dedupes_identical_content_under_a_different_name(tmp_path):
    assets_root = tmp_path / "assets"

    first = save_asset(make_upload("first-name.png", b"identical bytes"), assets_root)
    second = save_asset(make_upload("second-name.png", b"identical bytes"), assets_root)

    assert first == second
    assert len(list(assets_root.iterdir())) == 1


def test_save_asset_keeps_different_content_separate_even_with_same_name(tmp_path):
    assets_root = tmp_path / "assets"

    first = save_asset(make_upload("ref.png", b"content A"), assets_root)
    second = save_asset(make_upload("ref.png", b"content B"), assets_root)

    assert first != second
    assert len(list(assets_root.iterdir())) == 2


def test_build_ordered_paths_new_only_saves_each_file_once(tmp_path):
    assets_root = tmp_path / "assets"

    manifest = json.dumps([{"kind": "new"}, {"kind": "new"}])
    files = [make_upload("one.png", b"one"), make_upload("two.png", b"two")]

    paths = build_ordered_paths(manifest, files, assets_root)

    assert len(paths) == 2
    assert paths[0].endswith("__one.png")
    assert paths[1].endswith("__two.png")
    assert len(list(assets_root.iterdir())) == 2


def test_build_ordered_paths_reuses_existing_ref_without_duplicating(tmp_path):
    assets_root = tmp_path / "assets"

    # First job uploads a genuinely new file.
    first_paths = build_ordered_paths(
        json.dumps([{"kind": "new"}]), [make_upload("ref.png")], assets_root
    )
    existing_path = first_paths[0]

    # Second job reuses it by path -- no bytes attached, nothing to save.
    manifest = json.dumps([{"kind": "existing", "path": existing_path}])
    second_paths = build_ordered_paths(manifest, [], assets_root)

    assert second_paths == [existing_path]
    # still exactly one file in the shared store
    assert len(list(assets_root.iterdir())) == 1


def test_build_ordered_paths_mixed_manifest_preserves_order(tmp_path):
    assets_root = tmp_path / "assets"

    existing = build_ordered_paths(
        json.dumps([{"kind": "new"}]), [make_upload("first.png", b"first")], assets_root
    )[0]

    manifest = json.dumps([{"kind": "existing", "path": existing}, {"kind": "new"}])
    paths = build_ordered_paths(
        manifest, [make_upload("second.png", b"second")], assets_root
    )

    assert paths[0] == existing
    assert paths[1].endswith("__second.png")
    assert len(list(assets_root.iterdir())) == 2  # first.png + second.png, no dupes


def test_build_ordered_paths_malformed_manifest_treats_everything_as_new(tmp_path):
    assets_root = tmp_path / "assets"

    paths = build_ordered_paths("not json", [make_upload("a.png")], assets_root)

    assert len(paths) == 1
    assert paths[0].endswith("__a.png")


def test_resolve_existing_ref_rejects_path_outside_allowed_root(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    outside_file = tmp_path / "outside.png"
    outside_file.write_bytes(b"x")

    with pytest.raises(HTTPException) as exc_info:
        resolve_existing_ref(str(outside_file), uploads)

    assert exc_info.value.status_code == 400


def test_resolve_existing_ref_rejects_missing_file(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()

    with pytest.raises(HTTPException) as exc_info:
        resolve_existing_ref(str(uploads / "nope.png"), uploads)

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# register_generated_asset() -- a generated image/video (Nano Banana, Kling,
# Seedance, ...) must land in the same content-addressed store save_asset()
# writes into, so it shows up in the "reuse an uploaded ..." picker (/refs)
# immediately, just like a manual upload.
# ---------------------------------------------------------------------------


def test_register_generated_asset_writes_into_the_shared_store(tmp_path):
    assets_root = tmp_path / "assets"
    generated = tmp_path / "out" / "result.png"
    generated.parent.mkdir()
    generated.write_bytes(b"generated pixels")

    dest = register_generated_asset(generated, assets_root)

    assert dest is not None
    assert dest.parent == assets_root
    assert dest.name.endswith("__result.png")
    assert dest.read_bytes() == b"generated pixels"


def test_register_generated_asset_dedupes_against_an_existing_upload(tmp_path):
    assets_root = tmp_path / "assets"
    save_asset(make_upload("uploaded.png", b"identical bytes"), assets_root)

    generated = tmp_path / "out" / "generated.png"
    generated.parent.mkdir()
    generated.write_bytes(b"identical bytes")

    dest = register_generated_asset(generated, assets_root)

    # same content as the earlier upload -- reused, not duplicated
    assert len(list(assets_root.iterdir())) == 1
    assert dest.name.endswith("__uploaded.png")


def test_register_generated_asset_is_best_effort_on_a_missing_source(tmp_path):
    assets_root = tmp_path / "assets"

    # A job's generation succeeded already by the time this runs -- a
    # bookkeeping failure here (source file gone, unwritable dir, ...) must
    # never raise and blow up an otherwise-successful job.
    result = register_generated_asset(tmp_path / "does-not-exist.png", assets_root)

    assert result is None


# ---------------------------------------------------------------------------
# asset_card()/serve_job_file() with a remote-only result (local download
# never completed -- see job_status_payload's "preview"/nbp.py's remote_url
# fallback): the card must point straight at the hosted URL, and the
# `/files/{id}/{index}` route must never be asked to serve a local file that
# was never actually downloaded.
# ---------------------------------------------------------------------------


def test_asset_card_with_remote_url_points_view_and_download_at_it():
    card = asset_card(
        "clip.mp4", 0, "/kling3/files/abc123", remote_url="https://cdn.kie.ai/x.mp4"
    )

    assert card["view_url"] == "https://cdn.kie.ai/x.mp4"
    assert card["download_url"] == "https://cdn.kie.ai/x.mp4"


def test_asset_card_prefers_remote_url_even_when_a_local_path_also_exists():
    """A successful generation now keeps both a local path and remote_url
    (see seedance.py/kling.py/nbp.py) -- the kie.ai URL must still win, since
    it's faster than our own proxy and kie.ai keeps the file for 14 days."""
    card = asset_card(
        "clip.mp4", 0, "/kling3/files/abc123", remote_url="https://cdn.kie.ai/x.mp4"
    )

    assert card["view_url"] == "https://cdn.kie.ai/x.mp4"
    assert card["download_url"] == "https://cdn.kie.ai/x.mp4"
    assert card["view_url"] != "/kling3/files/abc123/0"


def test_serve_job_file_404s_for_a_result_with_no_local_path():
    job = {
        "status": "done",
        "result": [{"name": "x.png", "path": None, "remote_url": "https://cdn/x.png"}],
    }

    with pytest.raises(HTTPException) as exc_info:
        serve_job_file(job, 0)

    assert exc_info.value.status_code == 404
