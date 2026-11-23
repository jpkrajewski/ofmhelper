# Copyright (C) 2026 jpkrajewski

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "check_no_getattr",
    ROOT / "scripts" / "check_no_getattr.py",
)
assert SPEC is not None
assert SPEC.loader is not None
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def test_the_whole_repo_has_no_getattr():
    assert check.problems(ROOT, check.every_file(ROOT)) == []


def test_the_check_sees_getattr_under_every_name():
    source = (
        "from operator import attrgetter\n"
        "import builtins\n"
        "a = getattr(o, 'x')\n"
        "b = builtins.getattr(o, 'x')\n"
        "c = o.__getattribute__('x')\n"
        "d = 'getattr in a string'\n"
        "lookup = getattr\n"
        "def __getattribute__(self, name): ...\n"
    )

    spots = check.banned_spots("src/m.py", source)

    assert [spot.split(" is banned")[0] for spot in spots] == [
        "src/m.py:1: attrgetter",
        "src/m.py:3: getattr",
        "src/m.py:4: getattr",
        "src/m.py:5: __getattribute__",
        "src/m.py:7: getattr",
        "src/m.py:8: __getattribute__",
    ]
    assert check.banned_spots("tests/fixtures/page.py", source) == []


def test_vars_and_dunder_dict_are_banned_everywhere():
    source = (
        "fields = vars(args)\nsaved = record.__dict__\n"
        "def f(vars): ...\nf(__dict__=1)\n"
    )

    for path in ("src/m.py", "tests/unit/test_m.py", "scripts/m.py"):
        assert [s.split(" is banned")[0] for s in check.banned_spots(path, source)] == [
            f"{path}:1: vars",
            f"{path}:2: __dict__",
            f"{path}:3: vars",
            f"{path}:4: __dict__",
        ]
