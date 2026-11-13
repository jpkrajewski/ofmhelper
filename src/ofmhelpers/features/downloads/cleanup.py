"""The downloads sweep (features/jobs/sweeps.py, hourly). Nothing ever deleted
`downloads/`, and each video is on disk a second time in the asset store
(two mounts cannot hard-link): prod's disk sat at 84-93% (2026-11).

A downloaded video leaves this server (owner, 2026-11-11):
- `unused`: no job used it, a day (`keep_unused_s`) after it was last asked
  for (downloaded, its link pasted again, fetched again);
- `cloned`: a job names it in its params (a clone, any tool, any status),
  14 days (`keep_cloned_s`) after its last use;
- `max`: 30 days (`keep_max_s`) after it was first downloaded, whatever
  used it -- but never within a day of being asked for or used, so a video
  fetched again is there for a day.

What stays for good: its `downloads` row (`stored` false), its job, and its
poster (an image: its small preview), made now if missing -- a video whose
poster cannot be made is left this round. The card's Download and a clone's
Recreate fetch it again (`again.py`). What goes: the file in `downloads/`
(and its folder when empty), the 720p preview, and the asset store's copy
unless another stored download is the same file, a job's result is it, or
a clone used it within `keep_cloned_s` (the `max` rule: that clone's
Recreate still finds it). A video a running or waiting job names is never
touched; nor one whose own job is running (it is being fetched again).

A file already gone from disk is only marked (`stored` false).

`python -m ofmhelpers.features.downloads.cleanup` prints what a round would
remove right now (`dry_count`) and changes nothing.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.stores import downloads as store
from ofmhelpers.db.stores.jobs import naming_each
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.store import assets_root_for_user
from ofmhelpers.features.previews import render as previews
from ofmhelpers.integrations.downloaders.generic import DownloadConfig
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.downloads import Download
from ofmhelpers.schemas.persistence import Named

logger = get_logger(__name__)

# Rows looked at per read: one query says which of them a job names.
_BATCH = 50
_RULES = ("unused", "cloned", "max")


class _Judged(BaseModel):
    """One stored download as a round sees it."""

    file: Download
    on_disk: bool
    # Its copy in the asset store, when known.
    asset: str | None = None
    # What a job's params hold when it uses it: the content digest in the
    # asset copy's name (a count without the file: `__<name>`).
    fragment: str | None = None
    use: Named | None = None
    # Last asked for or used.
    idle_since: datetime
    # Why it goes now; None: it stays.
    rule: str | None = None

    @property
    def path(self) -> Path:
        return Path(self.file.file_path)

    @property
    def busy(self) -> bool:
        return self.use is not None and self.use.running


class Swept(BaseModel):
    """What one round did."""

    removed: int = 0
    unused: int = 0
    cloned: int = 0
    max: int = 0
    missing: int = 0
    no_poster: int = 0
    failed: int = 0
    freed: int = 0


class RuleCount(BaseModel):
    """Rows one rule would remove: how many, how many of those have their
    file on this disk, and the bytes those take (both copies)."""

    rule: str
    rows: int = 0
    on_disk: int = 0
    bytes: int = 0


class DryCount(BaseModel):
    """What a round would do right now, nothing changed. `missing`: rows
    whose file is not on this disk (a round marks them, whatever their
    rule). `busy`: due, but a running job names them."""

    looked_at: int = 0
    rules: list[RuleCount] = []
    missing: int = 0
    busy: int = 0
    gb: float = 0


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _asset_of(file: Download, path: Path) -> str | None:
    """The file's copy in its owner's part of the asset store, found by
    content (`<sha256>__<name>`), for a row that does not know it yet."""
    root = assets_root_for_user(file.user_id)
    found = next(root.glob(f"{_sha256(path)}__*"), None)
    return None if found is None else str(found)


def _digest(asset: str) -> str:
    return Path(asset).name.split("__", 1)[0]


def _look(file: Download, *, write: bool) -> _Judged:
    """`file` checked against the disk; with `write`, the asset copy found
    for a row that lacks it is saved on the row."""
    path = Path(file.file_path)
    on_disk = path.is_file()
    asset = file.asset_path
    if asset is None and on_disk:
        asset = _asset_of(file, path)
        if asset is not None and write:
            store.set_asset_path(file.id, asset)
    fragment = _digest(asset) if asset else None
    if fragment is None and not on_disk and not write:
        fragment = f"__{path.name}"
    return _Judged(
        file=file,
        on_disk=on_disk,
        asset=asset,
        fragment=fragment,
        idle_since=file.last_used_at,
    )


def _rule(seen: _Judged, now: datetime) -> str | None:
    cfg = settings.downloaders
    idle = now - seen.idle_since
    if idle <= timedelta(seconds=cfg.keep_unused_s):
        return None
    if now - seen.file.created_at > timedelta(seconds=cfg.keep_max_s):
        return "max"
    if seen.use is None or seen.use.used_at is None:
        return "unused"
    return "cloned" if idle > timedelta(seconds=cfg.keep_cloned_s) else None


def _judge(looked: list[_Judged], now: datetime, *, write: bool) -> list[_Judged]:
    """Each with what uses it (one query for them all) and its rule; with
    `write`, a use moves the row's `last_used_at`."""
    named = {
        n.fragment: n for n in naming_each([s.fragment for s in looked if s.fragment])
    }
    judged = []
    for seen in looked:
        use = named.get(seen.fragment) if seen.fragment else None
        idle_since = seen.idle_since
        if use is not None and use.used_at is not None:
            used_at = datetime.fromtimestamp(use.used_at, UTC)
            if used_at > idle_since:
                idle_since = used_at
                if write:
                    store.note_use(seen.file.id, used_at)
        known = seen.model_copy(update={"use": use, "idle_since": idle_since})
        judged.append(known.model_copy(update={"rule": _rule(known, now)}))
    return judged


def _size(path: Path) -> int:
    """The bytes deleting `path` gives back: none while another name (a
    hard link in the asset store) still holds them."""
    try:
        st = path.stat()
    except OSError:
        return 0
    return st.st_size if st.st_nlink == 1 else 0


def _delete(path: Path) -> int:
    freed = _size(path)
    path.unlink(missing_ok=True)
    return freed


def _keeps_asset(seen: _Judged, now: datetime) -> bool:
    """Whether the asset store's copy outlives the download: another stored
    download is the same file, a job's result is it, or a clone used it
    lately (only the `max` rule gets here with one)."""
    if seen.asset is None:
        return True
    if store.asset_shared(seen.file.id, seen.asset):
        return True
    use = seen.use
    if use is None:
        return False
    lately = now - timedelta(seconds=settings.downloaders.keep_cloned_s)
    used_lately = (
        use.used_at is not None and datetime.fromtimestamp(use.used_at, UTC) > lately
    )
    return use.in_result or used_lately


def _thumbnail(seen: _Judged) -> None:
    """What the card shows once the file is gone, made now if missing.
    Raises one of PREVIEW_ERRORS when it cannot be made."""
    shown = previews.ResultPreview(
        source=seen.path, job_id=seen.file.job_id, index=seen.file.position
    )
    kind = classify_kind(seen.path.name)
    if kind == "video":
        shown.poster()
    elif kind == "image":
        shown.make()


def _started_since(seen: _Judged) -> bool:
    """Read again, after the slow part (a poster): whether a job naming it
    is running now, or one started since it was judged."""
    if seen.fragment is None:
        return False
    for named in naming_each([seen.fragment]):
        seen_use = seen.use.used_at if seen.use else None
        if named.running or named.used_at != seen_use:
            return True
    return False


def _remove(seen: _Judged, now: datetime) -> int | None:
    """The video of one due row removed; the bytes freed, or None when it
    was asked for or used meanwhile and stays. The row is marked first (a
    paste arriving now fetches it again); a file that cannot be deleted
    puts the row back, for the next round."""
    file = seen.file
    if _started_since(seen) or not store.mark_removed(file.id, seen.idle_since, now):
        return None
    try:
        return _delete_files(seen, now)
    except OSError:
        store.put_back(file.id)
        raise


def _delete_files(seen: _Judged, now: datetime) -> int:
    file = seen.file
    if store.file_shared(file.id, file.file_path):
        # Someone else's card is this same file (a link reused, not
        # fetched again): this row is done with it, the file stays for theirs.
        return 0
    freed = _delete(seen.path)
    folder = seen.path.parent
    # Its own folder, once the last file of the link is gone (a carousel's
    # others may still be there); never the downloads root itself.
    if folder != DownloadConfig().output_dir and not any(folder.iterdir()):
        folder.rmdir()
    if classify_kind(seen.path.name) == "video":
        previews.preview_path(file.job_id, file.position, "video").unlink(
            missing_ok=True
        )
        previews.failed_path(file.job_id, file.position).unlink(missing_ok=True)
    if not _keeps_asset(seen, now):
        freed += _delete(Path(seen.asset or ""))
    return freed


def _sweep_one(seen: _Judged, now: datetime, done: Swept) -> None:
    if not seen.on_disk:
        if store.mark_removed(seen.file.id, seen.idle_since, now):
            done.missing += 1
        return
    if seen.rule is None or seen.busy:
        return
    try:
        _thumbnail(seen)
    except previews.PREVIEW_ERRORS:
        logger.warning(
            "downloads sweep: no poster of %s (job %s): the video stays",
            seen.path,
            seen.file.job_id,
            exc_info=True,
        )
        done.no_poster += 1
        return
    freed = _remove(seen, now)
    if freed is None:
        return
    done.removed += 1
    done.freed += freed
    setattr(done, seen.rule, getattr(done, seen.rule) + 1)


def _idle_before(now: datetime) -> datetime:
    return now - timedelta(seconds=settings.downloaders.keep_unused_s)


def _looked(batch: list[Download], done: Swept, *, write: bool) -> list[_Judged]:
    """One row that cannot be read (its file, its owner's store) is logged
    and left; the rest go on."""
    looked = []
    for file in batch:
        try:
            looked.append(_look(file, write=write))
        except Exception:
            logger.warning(
                "downloads sweep: download %s (job %s) not read",
                file.id,
                file.job_id,
                exc_info=True,
            )
            done.failed += 1
    return looked


def sweep(now: datetime | None = None) -> Swept:
    """One round at `now`: every stored download idle for a day is looked
    at, a batch at a time, and what its rule says goes."""
    now = now or datetime.now(UTC)
    done = Swept()
    after_id = 0
    while True:
        batch = store.idle(_idle_before(now), after_id, _BATCH)
        if not batch:
            break
        after_id = batch[-1].id
        for seen in _judge(_looked(batch, done, write=True), now, write=True):
            try:
                _sweep_one(seen, now, done)
            except Exception:  # one row must not stop the others
                logger.warning(
                    "downloads sweep: download %s (job %s) failed",
                    seen.file.id,
                    seen.file.job_id,
                    exc_info=True,
                )
                done.failed += 1
    return done


def remove_old() -> None:
    """The sweep's round."""
    done = sweep()
    logger.info(
        "downloads sweep: %s videos removed (%s unused, %s cloned, %s over the"
        " limit), %s already missing, %s kept without a poster, %s failed,"
        " %.2f GB freed",
        done.removed,
        done.unused,
        done.cloned,
        done.max,
        done.missing,
        done.no_poster,
        done.failed,
        done.freed / 1e9,
    )


def _bytes(seen: _Judged, now: datetime) -> int:
    """What removing it would free: the file, and the asset copy when that
    goes too and is a copy of its own."""
    size = seen.path.stat().st_size
    if seen.asset is None or _keeps_asset(seen, now):
        return size
    asset = Path(seen.asset)
    if not asset.is_file() or asset.samefile(seen.path):
        return size
    return size + asset.stat().st_size


def dry_count(now: datetime | None = None) -> DryCount:
    """What a round at `now` would remove, by rule; nothing is written or
    deleted. A row whose file is not on this disk (dev on a copy of prod's
    rows) is still counted under its rule, its use read from the file's
    name: an estimate there, exact where the files are."""
    now = now or datetime.now(UTC)
    rules = {name: RuleCount(rule=name) for name in _RULES}
    count = DryCount()
    unread = Swept()
    after_id = 0
    total = 0
    while True:
        batch = store.idle(_idle_before(now), after_id, _BATCH)
        if not batch:
            break
        after_id = batch[-1].id
        count.looked_at += len(batch)
        for seen in _judge(_looked(batch, unread, write=False), now, write=False):
            count.missing += not seen.on_disk
            if seen.rule is None:
                continue
            if seen.busy:
                count.busy += 1
                continue
            rule = rules[seen.rule]
            rule.rows += 1
            if seen.on_disk:
                rule.on_disk += 1
                rule.bytes += _bytes(seen, now)
                total += _bytes(seen, now)
    count.rules = list(rules.values())
    count.gb = round(total / 1e9, 3)
    return count


if __name__ == "__main__":
    print(dry_count().model_dump_json(indent=2))
