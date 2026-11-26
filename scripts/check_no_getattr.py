# Copyright (C) 2026 jpkrajewski

import ast
import sys
from pathlib import Path

ROOTS = ("src", "tests", "scripts", "alembic", ".claude")
SKIPPED = ("tests/fixtures/",)
BANNED = frozenset(
    {"getattr", "attrgetter", "__getattribute__", "getattr_static", "vars", "__dict__"},
)
FOUND = (
    "{spot}: {name} is banned (owner 2026-11-22): read the typed field, "
    "validate the object with from_attributes=True, dump the model, "
    "or look the name up in a mapping"
)


def banned_spots(relative: str, source: str) -> list[str]:
    if relative.startswith(SKIPPED):
        return []
    found = []
    for node in ast.walk(ast.parse(source)):
        match node:
            case (
                ast.Name(id=name)
                | ast.Attribute(attr=name)
                | ast.alias(name=name)
                | ast.FunctionDef(name=name)
                | ast.AsyncFunctionDef(name=name)
                | ast.arg(arg=name)
                | ast.keyword(arg=name)
            ) if name in BANNED:
                found.append((node.lineno, name))
    return [
        FOUND.format(spot=f"{relative}:{line}", name=name)
        for line, name in sorted(found)
    ]


def every_file(root: Path) -> list[str]:
    return [
        path.relative_to(root).as_posix()
        for top in ROOTS
        for path in sorted((root / top).rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def problems(root: Path, relatives: list[str]) -> list[str]:
    found = []
    for relative in relatives:
        path = root / relative
        if path.suffix != ".py" or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            found += banned_spots(relative.replace("\\", "/"), text)
        except SyntaxError as error:
            found.append(
                f"{relative}:{error.lineno}: does not parse, so it was not checked",
            )
    return found


def main(arguments: list[str]) -> int:
    found = problems(Path.cwd(), arguments)
    for spot in found:
        print(spot)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
