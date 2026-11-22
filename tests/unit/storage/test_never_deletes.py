import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src" / "ofmhelpers"
STORAGE_CLIENTS = [SRC / "integrations" / "storage"]
INTERFACE = SRC / "integrations" / "storage" / "base.py"
FOR_GOOD = re.compile(r"emptyTrash|permanently_delete|permanent_delete")
DELETE_CALL = re.compile(r"\.delete\(")
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


def test_storage_clients_never_call_a_delete() -> None:
    clients = [
        path
        for folder in STORAGE_CLIENTS
        for path in folder.rglob("*.py")
        if path.name != "fake.py"
    ]
    assert _lines(clients, DELETE_CALL) == []


def test_the_storage_interface_trashes_and_never_deletes() -> None:
    methods = METHOD.findall(INTERFACE.read_text("utf-8"))
    assert "trash_file" in methods
    assert [name for name in methods if "delete" in name] == []
