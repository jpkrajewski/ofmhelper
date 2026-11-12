"""The downloads sweep (features/downloads/cleanup.py; owner 2026-11-11): a
downloaded video no job used leaves the server a day after it was last asked
for, one a clone used 14 days after its last use, any 30 days after it was
downloaded. Its poster, its row and its card stay; a video a running job
names, or one whose poster cannot be made, is left alone.

No network and no ffmpeg: links "download" to small files, posters are
stand-ins (`download_files.py`), so this runs in the default suite."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from download_files import DAY, backdate, fake_downloads, fake_posters, row

from ofmhelpers.cache import QueueName, get_queue, get_redis
from ofmhelpers.db.models import DownloadRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import downloads as store
from ofmhelpers.db.stores.jobs import create_job, get_job, reopen_job
from ofmhelpers.features.assets import store as asset_store
from ofmhelpers.features.downloads import cleanup, media
from ofmhelpers.features.jobs import sweeps
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.previews import render as previews

LINK = "https://x.example/video/1"
OTHER = "https://x.example/video/2"
MINUTE = 60
_LINK = os.link


@pytest.fixture(autouse=True)
def assets(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "assets"
    monkeypatch.setattr(asset_store, "assets_root", lambda: root)
    return root


@pytest.fixture(autouse=True)
def two_mounts(monkeypatch) -> None:
    """As on the server: `downloads/` and the asset store are two mounts,
    a hard link between them fails and the store gets a copy of its own."""

    def no_link(*_args, **_kwargs) -> None:
        msg = "Invalid cross-device link"
        raise OSError(msg)

    monkeypatch.setattr(asset_store.os, "link", no_link)


@pytest.fixture(autouse=True)
def downloads(tmp_path, monkeypatch):
    return fake_downloads(monkeypatch, tmp_path)


@pytest.fixture
def posters(monkeypatch) -> None:
    fake_posters(monkeypatch)


def _download(assets: Path, *links: str) -> str:
    """A finished download of `links`, made just now by the agency."""
    job_id = create_job("download_media", {"urls": list(links)})
    kwargs = {
        "urls": list(links),
        "assets_root": str(assets),
        "user_id": None,
        "job_id": job_id,
    }
    run_job(job_id, media.run_downloads, kwargs)
    return job_id


def _in(seconds: float) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


def _clone_of(job_id: str, *, finished: bool = True) -> str:
    """A clone that names the download's copy in the asset store."""
    clone = create_job("clone", {"reference_videos": [row(job_id).asset_path]})
    if finished:
        run_job(clone, list, {})
    return clone


def _poster(job_id: str) -> Path:
    return previews.poster_path(job_id, 0)


def test_a_download_writes_one_row_per_file_with_its_copy_in_the_store(assets):
    job = _download(assets, LINK)

    file = row(job)

    assert (file.url, file.position, file.stored) == (LINK, 0, True)
    assert Path(file.file_path).read_bytes() == Path(file.asset_path).read_bytes()
    assert Path(file.asset_path).parent == assets
    assert file.user_id == get_job(job).user_id
    assert file.removed_at is None
    assert file.last_used_at == file.created_at


@pytest.mark.parametrize(("after_s", "stored"), [(-MINUTE, True), (MINUTE, False)])
def test_an_unused_video_goes_a_day_after_it_was_downloaded(
    assets, posters, after_s, stored
):
    job = _download(assets, LINK)
    file = row(job)

    done = cleanup.sweep(_in(DAY + after_s))

    assert row(job).stored is stored
    assert Path(file.file_path).exists() is stored
    assert Path(file.asset_path).exists() is stored
    assert (done.removed, done.unused) == ((0, 0) if stored else (1, 1))


def test_what_stays_of_a_removed_video_is_its_poster_its_row_and_its_job(
    assets, posters
):
    job = _download(assets, LINK)
    file = row(job)
    preview = previews.preview_path(job, 0, "video")
    preview.write_bytes(b"720p")
    size = Path(file.file_path).stat().st_size

    done = cleanup.sweep(_in(2 * DAY))

    assert not Path(file.file_path).exists()
    assert not Path(file.file_path).parent.exists()  # its own folder, now empty
    assert not Path(file.asset_path).exists()
    assert not preview.exists()
    assert _poster(job).read_bytes() == b"RIFF"
    after = row(job)
    assert (after.stored, after.file_path, after.asset_path) == (
        False,
        file.file_path,
        file.asset_path,
    )
    assert after.removed_at is not None
    assert get_job(job).status == "done"
    # Both copies: the two mounts cannot hard-link, each is its own file.
    assert done.freed == 2 * size


def test_a_hard_linked_copy_is_counted_once(assets, posters, monkeypatch):
    """One mount (dev, a test folder): the store's file is the download's
    under a second name, and the bytes come back with the last name."""
    monkeypatch.setattr(asset_store.os, "link", _LINK)
    job = _download(assets, LINK)
    file = row(job)
    assert Path(file.asset_path).samefile(file.file_path)
    size = Path(file.file_path).stat().st_size

    done = cleanup.sweep(_in(2 * DAY))

    assert not Path(file.asset_path).exists()
    assert done.freed == size


@pytest.mark.parametrize(("after_s", "stored"), [(-MINUTE, True), (MINUTE, False)])
def test_a_video_a_clone_used_goes_14_days_after_that_use(
    assets, posters, after_s, stored
):
    job = _download(assets, LINK)
    file = row(job)
    _clone_of(job)

    early = cleanup.sweep(_in(2 * DAY))
    done = cleanup.sweep(_in(14 * DAY + after_s))

    assert early.removed == 0
    assert row(job).stored is stored
    assert Path(file.file_path).exists() is stored
    assert Path(file.asset_path).exists() is stored
    assert (done.removed, done.cloned) == ((0, 0) if stored else (1, 1))


def test_a_use_moves_last_used_to_the_clones_start(assets, posters):
    job = _download(assets, LINK)
    backdate(job, 3 * DAY)
    clone = _clone_of(job)

    cleanup.sweep(_in(2 * DAY))

    used_at = datetime.fromtimestamp(get_job(clone).created_at, UTC)
    assert row(job).last_used_at == used_at
    assert row(job).stored is True


@pytest.mark.parametrize(("age_s", "stored"), [(-MINUTE, True), (MINUTE, False)])
def test_any_video_goes_30_days_after_it_was_downloaded(assets, posters, age_s, stored):
    """A clone used it two days before the round: far from its 14 days, so
    only the 30-day rule can take it. The clone's own copy (the asset
    store's) stays for its Recreate; the download's goes."""
    job = _download(assets, LINK)
    backdate(job, 28 * DAY + age_s)
    file = row(job)
    _clone_of(job)

    done = cleanup.sweep(_in(2 * DAY))

    assert row(job).stored is stored
    assert Path(file.file_path).exists() is stored
    assert Path(file.asset_path).exists()
    assert (done.removed, done.max) == ((0, 0) if stored else (1, 1))


def test_a_video_a_running_job_names_is_never_removed(assets, posters):
    job = _download(assets, LINK)
    file = row(job)
    clone = _clone_of(job, finished=False)

    waiting = cleanup.sweep(_in(40 * DAY))

    assert waiting.removed == 0
    assert row(job).stored is True
    assert Path(file.file_path).exists()
    assert Path(file.asset_path).exists()

    run_job(clone, list, {})
    done = cleanup.sweep(_in(40 * DAY))

    assert done.removed == 1
    assert row(job).stored is False


def test_a_download_being_fetched_again_is_left_alone(assets, posters):
    """Its own job is running: the files are being written."""
    job = _download(assets, LINK)
    file = row(job)
    reopen_job(job)

    done = cleanup.sweep(_in(2 * DAY))

    assert done.removed == 0
    assert Path(file.file_path).exists()


def test_a_video_whose_poster_cannot_be_made_stays(assets, monkeypatch):
    def no_poster(*_args, **_kwargs) -> None:
        msg = "ffmpeg could not read it"
        raise RuntimeError(msg)

    monkeypatch.setattr(previews, "write_video_thumb", no_poster)
    job = _download(assets, LINK)
    file = row(job)

    done = cleanup.sweep(_in(2 * DAY))

    assert (done.removed, done.no_poster) == (0, 1)
    assert row(job).stored is True
    assert Path(file.file_path).exists()
    assert Path(file.asset_path).exists()
    assert not _poster(job).exists()


def test_a_poster_already_made_is_not_made_again(assets, posters, monkeypatch):
    job = _download(assets, LINK)
    assert _poster(job).exists()  # made when the job finished

    def not_again(*_args, **_kwargs) -> None:
        msg = "the poster is there already"
        raise AssertionError(msg)

    monkeypatch.setattr(previews, "write_video_thumb", not_again)

    done = cleanup.sweep(_in(2 * DAY))

    assert done.removed == 1


def test_a_file_already_gone_from_disk_is_only_marked(assets, posters):
    job = _download(assets, LINK)
    file = row(job)
    Path(file.file_path).unlink()

    done = cleanup.sweep(_in(2 * DAY))

    assert (done.removed, done.missing) == (0, 1)
    after = row(job)
    assert after.stored is False
    assert after.removed_at is not None
    # Nothing else was touched: its copy in the store is another file's business.
    assert Path(file.asset_path).exists()


def test_an_image_keeps_its_small_preview(assets, posters):
    job = _download(assets, "https://x.example/photo/1")
    file = row(job)

    done = cleanup.sweep(_in(2 * DAY))

    assert done.removed == 1
    assert not Path(file.file_path).exists()
    assert previews.preview_path(job, 0, "image").read_bytes() == b"RIFF"


def test_one_row_that_fails_does_not_stop_the_rest(
    assets, posters, monkeypatch, caplog
):
    stuck = _download(assets, LINK)
    fine = _download(assets, OTHER)
    stuck_file = Path(row(stuck).file_path)
    delete = cleanup._delete

    def delete_but_one(path: Path) -> int:
        if path == stuck_file:
            msg = "device busy"
            raise PermissionError(msg)
        return delete(path)

    monkeypatch.setattr(cleanup, "_delete", delete_but_one)

    done = cleanup.sweep(_in(2 * DAY))

    assert (done.removed, done.failed) == (1, 1)
    assert row(fine).stored is False
    # Still there, and still said to be: the next round tries again.
    assert stuck_file.exists()
    assert row(stuck).stored is True
    assert row(stuck).removed_at is None
    assert f"job {stuck}" in caplog.text
    assert "device busy" in caplog.text


def test_the_same_video_downloaded_twice_keeps_its_one_copy_in_the_store(
    assets, posters
):
    """Two downloads of the same bytes share one file in the store: it goes
    with the last of them."""
    first = _download(assets, LINK)
    second = _download(assets, OTHER)
    Path(row(second).file_path).write_bytes(Path(row(first).file_path).read_bytes())
    shared = row(first).asset_path
    store.set_asset_path(row(second).id, shared)
    backdate(first, 2 * DAY)

    cleanup.sweep(_in(0))

    assert row(first).stored is False
    assert Path(shared).exists()

    cleanup.sweep(_in(2 * DAY))

    assert row(second).stored is False
    assert not Path(shared).exists()


def test_the_round_logs_what_it_did(assets, posters, caplog):
    job = _download(assets, LINK)
    backdate(job, 2 * DAY)
    caplog.set_level("INFO")

    cleanup.remove_old()

    assert (
        "downloads sweep: 1 videos removed (1 unused, 0 cloned, 0 over the limit)"
        in (caplog.text)
    )


def test_the_dry_count_says_what_a_round_would_remove_and_removes_nothing(
    assets, posters
):
    unused = _download(assets, LINK)
    cloned = _download(assets, OTHER)
    _clone_of(cloned)
    files = [Path(row(unused).file_path), Path(row(cloned).file_path)]
    size = files[0].stat().st_size

    count = cleanup.dry_count(_in(2 * DAY))

    assert count.looked_at == 2
    assert {r.rule: (r.rows, r.on_disk, r.bytes) for r in count.rules} == {
        "unused": (1, 1, 2 * size),
        "cloned": (0, 0, 0),
        "max": (0, 0, 0),
    }
    assert (count.missing, count.busy) == (0, 0)
    assert all(path.exists() for path in files)
    assert row(unused).stored is True
    # Not even the use is written.
    assert row(cloned).last_used_at == row(cloned).created_at


def test_the_dry_count_reads_a_use_from_the_name_when_the_file_is_not_here(
    assets, posters
):
    """Dev on a copy of prod's rows: no file, no `asset_path` yet. A clone
    that names `<sha256>__<name>` still counts as its use."""
    job = _download(assets, LINK)
    file = row(job)
    _clone_of(job)
    with session_scope() as s:
        s.get(DownloadRow, file.id).asset_path = None
    Path(file.file_path).unlink()

    early = cleanup.dry_count(_in(2 * DAY))
    late = cleanup.dry_count(_in(15 * DAY))

    assert (early.missing, sum(r.rows for r in early.rules)) == (1, 0)
    assert {r.rule: (r.rows, r.on_disk) for r in late.rules}["cloned"] == (1, 0)


@pytest.fixture
def real_queue(monkeypatch):
    """Jobs wait in Redis from here on, instead of running inline."""

    def switch_on() -> None:
        monkeypatch.setenv("OFM_RQ_ASYNC", "true")
        get_redis().flushdb()

    yield switch_on
    get_redis().flushdb()


def test_the_sweep_is_put_in_the_workers_line_once(real_queue):
    """Its disk and ffmpeg work is a worker's; a round while the last one
    still waits adds nothing."""
    (sweep,) = [s for s in sweeps.sweeps() if s.name == "downloads cleanup"]
    real_queue()

    sweep.round()
    sweep.round()

    queue = get_queue(QueueName.PREVIEWS)
    assert queue.get_job_ids() == ["sweep-downloads-cleanup"]
    queue.empty()
    sweep.round()
    assert queue.count == 1
