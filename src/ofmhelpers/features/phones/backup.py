"""The phones backup: the whole /phones History in one encrypted JSON file
(owner 2026-11-13).

The database records every change to a phone, a login or a password at the
moment it happens (`phone_revisions`, alembic b7c8d9e0f1a3). This copies
that History out of the database: `dump()` (the `phones backup` sweep, every
`OFM_PHONES_BACKUP_SWEEP_S`, 5 min) writes EVERY revision of EVERY owner --
whose, when, what changed, and their rows of `devices`, `logins` and
`device_logins` after it -- as one JSON document to
`<uploads>/backups/phones/phones-YYYY-MM-DD.json.enc`. It writes only when a
phone changed since the last file (`last-revision` next to the files holds
the newest revision already written), so the day's file is at most one
sweep behind the database. One file a day, the newest
`OFM_PHONES_BACKUP_KEEP` (30) days kept; each file is complete on its own.

Encrypted twice, with the one vault key (`OFM_VAULT_KEY`, Fernet = AES-128
+ HMAC): the passwords and backup codes inside are the columns' own
encrypted text, and the whole document is encrypted again, so the file
shows no phone, no username, nothing. Without the key nobody reads it:
keep the key somewhere that is not this server. The file sits on the same
disk as the database; copying it elsewhere is by hand.

Reading one back, in the app container (the key in its environment):

    python -m ofmhelpers.features.phones.backup load <file>

decrypts it and puts every revision the History lacks back under its
owner, with its time and words. It changes no phone: each person then
opens /phones, History, and restores the line they want (passwords come
back readable there, to them alone). Loading twice adds nothing.

    python -m ofmhelpers.features.phones.backup dump

writes today's file now, changed or not.
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.db.encrypted import decrypt, encrypt
from ofmhelpers.db.stores import phone_revisions as store
from ofmhelpers.features.assets.store import uploads_root
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.schemas.phone_revisions import DumpLoaded, DumpWritten, PhoneDump

logger = get_logger(__name__)

_PREFIX = "phones-"
_SUFFIX = ".json.enc"
# The newest revision the last file holds: a number, no secret.
_MARKER = "last-revision"


def backups_dir() -> Path:
    return uploads_root() / "backups" / "phones"


def _written(folder: Path) -> int:
    """The newest revision already in a file; -1 when none was written (or
    the marker is unreadable: the next round then writes)."""
    try:
        return int((folder / _MARKER).read_text(encoding="ascii"))
    except OSError, ValueError:
        return -1


def _drop_old(folder: Path, keep: int) -> None:
    """All but the newest `keep` files (their names sort by day)."""
    files = sorted(folder.glob(f"{_PREFIX}*{_SUFFIX}"))
    for old in files[: max(len(files) - keep, 0)]:
        old.unlink()
        logger.info("phones backup %s removed", old.name)


def dump(*, always: bool = False) -> DumpWritten:
    """Today's file with the whole History, written whole (a later run of
    the day replaces it); nothing when no phone changed since the last one,
    unless `always`. Raises `VaultError` without the vault key: no plaintext
    backup, ever."""
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
    """A backup file's content; `VaultError` when the key is not the one it
    was written with."""
    return PhoneDump.model_validate_json(decrypt(path.read_text(encoding="ascii")))


def load(path: Path) -> DumpLoaded:
    """Every revision in the file that the History lacks, back under its
    owner. No phone changes."""
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
