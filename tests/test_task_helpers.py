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
    build_ordered_paths,
    resolve_existing_ref,
    save_asset,
    save_upload,
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
