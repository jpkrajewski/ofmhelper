# Copyright (C) 2026 jpkrajewski

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src" / "ofmhelpers"
STORAGE_CLIENTS = [SRC / "integrations" / "storage"]
INTERFACE = SRC / "integrations" / "storage" / "base.py"
FOR_GOOD = re.compile(r"emptyTrash|permanently_delete|permanent_delete")
DROPBOX_CLIENT = SRC / "integrations" / "storage" / "dropbox" / "client.py"
METHOD = re.compile(r"^\s+def (\w+)\(", re.MULTILINE)


def _lines(paths: list[Path], pattern: re.Pattern[str]) -> list[str]:
    return [
        f"{path.relative_to(SRC)}:{number}"
        for path in paths
        for number, line in enumerate(path.read_text("utf-8").splitlines(), 1)
        if pattern.search(line)
    ]


def test_no_code_empties_a_bin_or_deletes_for_good() -> None:
    assert _lines(list(SRC.rglob("*.py")), FOR_GOOD) == []


def _is_delete(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    return call.func.attr == "delete" or call.func.attr.startswith("files_delete")


def _deletes(source: str, *, may_trash_file: bool) -> list[int]:
    found: set[int] = set()
    exempt: set[int] = set()
    tree = ast.parse(source)
    for function in ast.walk(tree):
        if (
            may_trash_file
            and isinstance(function, ast.FunctionDef)
            and function.name == "trash_file"
        ):
            for call in ast.walk(function):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "files_delete_v2"
                ):
                    exempt.add(call.lineno)
    for call in ast.walk(tree):
        if (
            isinstance(call, ast.Call)
            and _is_delete(call)
            and call.lineno not in exempt
        ):
            found.add(call.lineno)
    return sorted(found)


def test_storage_clients_never_call_a_delete() -> None:
    clients = [
        path
        for folder in STORAGE_CLIENTS
        for path in folder.rglob("*.py")
        if path.name != "fake.py"
    ]
    found = {
        path: _deletes(path.read_text("utf-8"), may_trash_file=path == DROPBOX_CLIENT)
        for path in clients
    }
    assert {
        path.relative_to(SRC): lines for path, lines in found.items() if lines
    } == {}


def test_only_the_trash_file_method_may_call_files_delete_v2() -> None:
    source = (
        "class C:\n"
        "    def trash_file(self, x):\n"
        "        self.api.files_delete_v2(x)\n"
        "    def other(self, x):\n"
        "        self.api.files_delete_v2(x)\n"
        "        self.api.files_delete(x)\n"
        "        self.files.delete(x)\n"
    )
    assert _deletes(source, may_trash_file=True) == [5, 6, 7]
    assert _deletes(source, may_trash_file=False) == [3, 5, 6, 7]


def test_the_storage_interface_trashes_and_never_deletes() -> None:
    methods = METHOD.findall(INTERFACE.read_text("utf-8"))
    assert "trash_file" in methods
    assert [name for name in methods if "delete" in name] == []
