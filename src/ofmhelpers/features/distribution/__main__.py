# Copyright (C) 2026 jpkrajewski

import argparse
import json
from pathlib import Path

from ofmhelpers.features.distribution import presets
from ofmhelpers.features.distribution.report import report
from ofmhelpers.log import configure_logging


def _report(args: argparse.Namespace) -> None:
    found = report(args.days, args.owner)
    for row in [*found.days, *found.models, *found.accounts]:
        print(row.model_dump_json())


def _preset(args: argparse.Namespace) -> None:
    saved = presets.load(args.owner, Path(args.file))
    print(
        json.dumps(
            {
                "saved": saved.key,
                "title": saved.title,
                "captions": len(saved.descriptions),
            },
        ),
    )


def _presets(args: argparse.Namespace) -> None:
    for found in presets.presets_of(args.owner):
        print(
            json.dumps(
                {
                    "key": found.key,
                    "title": found.title,
                    "creator": found.creator,
                    "captions": len(found.descriptions),
                    "instagram": found.instagram,
                },
            ),
        )


def _main() -> None:
    parser = argparse.ArgumentParser(description="Pinterest distribution.")
    commands = parser.add_subparsers(dest="command", required=True)
    days = commands.add_parser("report", help="day by day, per account")
    days.add_argument("--days", type=int, default=7)
    days.add_argument(
        "--owner",
        type=int,
        default=None,
        help="one person's (default: all)",
    )
    days.set_defaults(run=_report)
    one = commands.add_parser("preset", help="save a model's preset (JSON file)")
    one.add_argument("file")
    one.add_argument("--owner", type=int, required=True)
    one.set_defaults(run=_preset)
    listed = commands.add_parser("presets", help="list a person's presets")
    listed.add_argument("--owner", type=int, required=True)
    listed.set_defaults(run=_presets)
    args = parser.parse_args()
    configure_logging()
    args.run(args)


if __name__ == "__main__":
    _main()
