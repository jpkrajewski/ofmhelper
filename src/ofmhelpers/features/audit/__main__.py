import argparse

from ofmhelpers.db.audit_log import store as audit_log
from ofmhelpers.log import configure_logging
lazy from ofmhelpers.db.audit_log.schema import AuditEntry


def _found(args: argparse.Namespace) -> list[AuditEntry]:
    if args.command == "row":
        return audit_log.history(args.table, args.row_id, args.limit)
    if args.command == "actor":
        return audit_log.by_actor(args.id, args.limit)
    return audit_log.of_owner(args.id, args.limit)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Who did what (audit_log).")
    limit = argparse.ArgumentParser(add_help=False)
    limit.add_argument("--limit", type=int, default=100)
    commands = parser.add_subparsers(dest="command", required=True)
    row = commands.add_parser("row", parents=[limit], help="every change to one row")
    row.add_argument("table")
    row.add_argument("row_id", help='its key values joined by ":"')
    actor = commands.add_parser("actor", parents=[limit], help="what one person did")
    actor.add_argument("id", type=int)
    owner = commands.add_parser(
        "owner", parents=[limit], help="what happened to one person's data"
    )
    owner.add_argument("id", type=int)
    args = parser.parse_args()
    configure_logging()
    for entry in _found(args):
        print(entry.model_dump_json())


if __name__ == "__main__":
    _main()
