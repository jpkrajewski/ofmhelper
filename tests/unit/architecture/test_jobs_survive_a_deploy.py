import ast
from pathlib import Path

from ofmhelpers.background import recovery
from ofmhelpers.background.registry import (
    DRAINED,
    RECOVERED_BY_THEIR_SWEEP,
    RESUMABLE,
)

SRC = Path(__file__).resolve().parents[3] / "src" / "ofmhelpers"


def _is_task_name(target: ast.expr) -> bool:
    return isinstance(target, ast.Name) and (
        target.id == "TASK" or target.id.lower().endswith("_task")
    )


def _task_names() -> set[str]:
    found = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        class_fields = [
            field
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name.endswith("Constants")
            for field in node.body
        ]
        for node in [*tree.body, *class_fields]:
            value: ast.expr | None
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            else:
                continue
            if (
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and any(_is_task_name(t) for t in targets)
            ):
                found.add(value.value)
    return found


def test_every_job_task_carries_on_after_a_deploy():
    carries_on = (
        set(RESUMABLE) | set(recovery.RESTARTS) | DRAINED | RECOVERED_BY_THEIR_SWEEP
    )

    assert _task_names() - carries_on == set()


def test_the_reader_finds_tasks_of_every_kind():
    assert {"trials", "clone", "uniqify", "pinterest", "drive_sync"} <= _task_names()
