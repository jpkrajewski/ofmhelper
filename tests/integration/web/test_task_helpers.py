import io
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from ofmhelpers.db.jobs.schema import Job
from ofmhelpers.features.assets import ref_usage
from ofmhelpers.web.routers.task_helpers import (
    asset_card,
    build_ordered_paths,
    register_generated_asset,
    resolve_existing_ref,
    save_asset,
    save_upload,
    serve_job_file,
)

USER = 1


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

    paths = build_ordered_paths(manifest, files, assets_root, user_id=USER)

    assert len(paths) == 2
    assert paths[0].endswith("__one.png")
    assert paths[1].endswith("__two.png")
    assert len(list(assets_root.iterdir())) == 2


def test_build_ordered_paths_reuses_existing_ref_without_duplicating(tmp_path):
    assets_root = tmp_path / "assets"

    first_paths = build_ordered_paths(
        json.dumps([{"kind": "new"}]),
        [make_upload("ref.png")],
        assets_root,
        user_id=USER,
    )
    existing_path = first_paths[0]

    manifest = json.dumps([{"kind": "existing", "path": existing_path}])
    second_paths = build_ordered_paths(manifest, [], assets_root, user_id=USER)

    assert second_paths == [existing_path]
    assert len(list(assets_root.iterdir())) == 1


def test_build_ordered_paths_mixed_manifest_preserves_order(tmp_path):
    assets_root = tmp_path / "assets"

    existing = build_ordered_paths(
        json.dumps([{"kind": "new"}]),
        [make_upload("first.png", b"first")],
        assets_root,
        user_id=USER,
    )[0]

    manifest = json.dumps([{"kind": "existing", "path": existing}, {"kind": "new"}])
    paths = build_ordered_paths(
        manifest, [make_upload("second.png", b"second")], assets_root, user_id=USER
    )

    assert paths[0] == existing
    assert paths[1].endswith("__second.png")
    assert len(list(assets_root.iterdir())) == 2


def test_build_ordered_paths_refuses_a_malformed_manifest(tmp_path):
    assets_root = tmp_path / "assets"

    with pytest.raises(HTTPException) as exc_info:
        build_ordered_paths(
            "not json", [make_upload("a.png")], assets_root, user_id=USER
        )

    assert exc_info.value.status_code == 400
    assert not assets_root.exists()


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


def test_resolve_existing_ref_does_not_write_to_the_store(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    existing = uploads / "hash__face.png"
    existing.write_bytes(b"x")
    os.utime(existing, (1_700_000_000, 1_700_000_000))

    assert resolve_existing_ref(str(existing), uploads) == existing.resolve()
    assert existing.stat().st_mtime == 1_700_000_000


def test_reusing_a_ref_records_it_as_used(tmp_path, monkeypatch):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    existing = uploads / "hash__face.png"
    existing.write_bytes(b"x")
    recorded = []
    monkeypatch.setattr(
        ref_usage, "record_use", lambda user, path: recorded.append((user, path))
    )

    build_ordered_paths(
        json.dumps([{"kind": "existing", "path": str(existing)}]),
        [],
        uploads,
        user_id=USER,
    )

    assert recorded == [(USER, existing.resolve())]


def test_a_new_upload_is_recorded_as_used_too(tmp_path, monkeypatch):
    assets_root = tmp_path / "assets"
    first = build_ordered_paths(
        json.dumps([{"kind": "new"}]),
        [make_upload("face.png")],
        assets_root,
        user_id=USER,
    )
    recorded = []
    monkeypatch.setattr(
        ref_usage, "record_use", lambda user, path: recorded.append((user, path))
    )

    again = build_ordered_paths(
        json.dumps([{"kind": "new"}, {"kind": "existing", "path": first[0]}]),
        [make_upload("face-copy.png")],
        assets_root,
        user_id=USER,
    )

    assert again == [first[0], first[0]]
    assert recorded == [(USER, Path(first[0])), (USER, Path(first[0]))]


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

    assert len(list(assets_root.iterdir())) == 1
    assert dest.name.endswith("__uploaded.png")


def test_register_generated_asset_is_best_effort_on_a_missing_source(tmp_path):
    assets_root = tmp_path / "assets"

    result = register_generated_asset(tmp_path / "does-not-exist.png", assets_root)

    assert result is None


def test_asset_card_with_remote_url_points_view_and_download_at_it():
    card = asset_card(
        "clip.mp4", 0, "/kling3/files/abc123", remote_url="https://cdn.kie.ai/x.mp4"
    )

    assert card.view_url == "https://cdn.kie.ai/x.mp4"
    assert card.download_url == "https://cdn.kie.ai/x.mp4"


def test_asset_card_prefers_remote_url_even_when_a_local_path_also_exists():
    card = asset_card(
        "clip.mp4", 0, "/kling3/files/abc123", remote_url="https://cdn.kie.ai/x.mp4"
    )

    assert card.view_url == "https://cdn.kie.ai/x.mp4"
    assert card.download_url == "https://cdn.kie.ai/x.mp4"
    assert card.view_url != "/kling3/files/abc123/0"


def test_serve_job_file_404s_for_a_result_with_no_local_path():
    job = Job(
        id="j1",
        task="nanobanana",
        created_at=0.0,
        status="done",
        result=[{"name": "x.png", "path": None, "remote_url": "https://cdn/x.png"}],
    )

    with pytest.raises(HTTPException) as exc_info:
        serve_job_file(job, 0)

    assert exc_info.value.status_code == 404
