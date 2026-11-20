import argparse
from datetime import UTC, datetime
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.db.encrypted import decrypt, encrypt
from ofmhelpers.db.phone_revisions import store
from ofmhelpers.db.phone_revisions.schema import DumpLoaded, DumpWritten, PhoneDump
from ofmhelpers.features.assets.store import uploads_root
from ofmhelpers.log import configure_logging, get_logger

logger = get_logger(__name__)

_PREFIX = "phones-"
_SUFFIX = ".json.enc"
_MARKER = "last-revision"


def backups_dir() -> Path:
    return uploads_root() / "backups" / "phones"


def _written(folder: Path) -> int:
    try:
        return int((folder / _MARKER).read_text(encoding="ascii"))
    except FileNotFoundError:
        logger.debug("phones backup: none written in %s yet", folder, exc_info=True)
        return -1
    except OSError, ValueError:
        logger.warning("phones backup: marker in %s unreadable", folder, exc_info=True)
        return -1


def _drop_old(folder: Path, keep: int) -> None:
    files = sorted(folder.glob(f"{_PREFIX}*{_SUFFIX}"))
    for old in files[: max(len(files) - keep, 0)]:
        old.unlink()
        logger.info("phones backup %s removed", old.name)


def dump(*, always: bool = False) -> DumpWritten:
    folder = backups_dir()
    newest = store.newest_id()
    if not always and newest == _written(folder):
        return DumpWritten(path=None)
    now = datetime.now(tz=UTC)
    states = store.history()
    sealed = encrypt(
        PhoneDump(taken_at=now, newest=newest, states=states).model_dump_json()
    )
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_PREFIX}{now.date().isoformat()}{_SUFFIX}"
    partial = path.with_suffix(".part")
    partial.write_text(sealed, encoding="ascii")
    partial.replace(path)
    (folder / _MARKER).write_text(str(newest), encoding="ascii")
    _drop_old(folder, settings.vault.backup_keep)
    owners = len({state.user_id for state in states})
    logger.info(
        "phones backup %s written: %d revisions of %d owners",
        path.name,
        len(states),
        owners,
    )
    return DumpWritten(path=str(path), revisions=len(states), owners=owners)


def read(path: Path) -> PhoneDump:
    return PhoneDump.model_validate_json(decrypt(path.read_text(encoding="ascii")))


def load(path: Path) -> DumpLoaded:
    added = [store.load(state) for state in read(path).states]
    revisions = [revision for revision in added if revision is not None]
    logger.info("phones backup %s loaded: %d revisions", path.name, len(revisions))
    return DumpLoaded(revisions=revisions)


def _main() -> None:
    parser = argparse.ArgumentParser(description="The phones backup file.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("dump", help="write today's backup file now")
    loading = commands.add_parser("load", help="put a file's History back")
    loading.add_argument("file", type=Path)
    args = parser.parse_args()
    configure_logging()
    done = dump(always=True) if args.command == "dump" else load(args.file)
    print(done.model_dump_json())


if __name__ == "__main__":
    _main()
