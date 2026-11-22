import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.downloads import store
from ofmhelpers.db.downloads.schema import Download
from ofmhelpers.db.jobs.schema import Named
from ofmhelpers.db.jobs.store import naming_each
from ofmhelpers.features.assets.store import assets_root_for_user
from ofmhelpers.features.kinds import classify_kind
from ofmhelpers.features.previews import render as previews
from ofmhelpers.integrations.downloaders.generic import DownloadConfig
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_BATCH = 50
type Rule = Literal["unused", "cloned", "max"]
_RULES: tuple[Rule, ...] = ("unused", "cloned", "max")


class _Judged(BaseModel):
    file: Download
    on_disk: bool
    asset: str | None = None
    fragment: str | None = None
    use: Named | None = None
    idle_since: datetime
    rule: Rule | None = None

    @property
    def path(self) -> Path:
        return Path(self.file.file_path)

    @property
    def busy(self) -> bool:
        return self.use is not None and self.use.running


class Swept(BaseModel):
    removed: int = 0
    unused: int = 0
    cloned: int = 0
    max: int = 0
    missing: int = 0
    no_poster: int = 0
    failed: int = 0
    freed: int = 0


class RuleCount(BaseModel):
    rule: str
    rows: int = 0
    on_disk: int = 0
    bytes: int = 0


class DryCount(BaseModel):
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
    root = assets_root_for_user(file.user_id)
    found = next(root.glob(f"{_sha256(path)}__*"), None)
    return None if found is None else str(found)


def _digest(asset: str) -> str:
    return Path(asset).name.split("__", 1)[0]


def _look(file: Download, *, write: bool) -> _Judged:
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


def _rule(seen: _Judged, now: datetime) -> Rule | None:
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
    try:
        st = path.stat()
    except FileNotFoundError:
        logger.debug("download %s gone meanwhile", path, exc_info=True)
        return 0
    except OSError:
        logger.warning("download %s not read", path, exc_info=True)
        return 0
    return st.st_size if st.st_nlink == 1 else 0


def _delete(path: Path) -> int:
    freed = _size(path)
    path.unlink(missing_ok=True)
    return freed


def _keeps_asset(seen: _Judged, now: datetime) -> bool:
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
    shown = previews.ResultPreview(
        source=seen.path, job_id=seen.file.job_id, index=seen.file.position
    )
    kind = classify_kind(seen.path.name)
    if kind == "video":
        shown.poster()
    elif kind == "image":
        shown.make()


def _started_since(seen: _Judged) -> bool:
    if seen.fragment is None:
        return False
    for named in naming_each([seen.fragment]):
        seen_use = seen.use.used_at if seen.use else None
        if named.running or named.used_at != seen_use:
            return True
    return False


def _remove(seen: _Judged, now: datetime) -> int | None:
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
        return 0
    freed = _delete(seen.path)
    folder = seen.path.parent
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
    match seen.rule:
        case "unused":
            done.unused += 1
        case "cloned":
            done.cloned += 1
        case "max":
            done.max += 1


def _idle_before(now: datetime) -> datetime:
    return now - timedelta(seconds=settings.downloaders.keep_unused_s)


def _looked(batch: list[Download], done: Swept, *, write: bool) -> list[_Judged]:
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
            except Exception:
                logger.warning(
                    "downloads sweep: download %s (job %s) failed",
                    seen.file.id,
                    seen.file.job_id,
                    exc_info=True,
                )
                done.failed += 1
    return done


def remove_old() -> None:
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
    size = seen.path.stat().st_size
    if seen.asset is None or _keeps_asset(seen, now):
        return size
    asset = Path(seen.asset)
    if not asset.is_file() or asset.samefile(seen.path):
        return size
    return size + asset.stat().st_size


def dry_count(now: datetime | None = None) -> DryCount:
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
