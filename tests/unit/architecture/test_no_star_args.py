# Copyright (C) 2026 jpkrajewski

import ast
from textwrap import dedent

from test_architecture import check, under

FUNCTIONS = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
owner_allowed_file_2026_11_23 = "db/cached_repository.py::"
owner_allowed_function_2026_11_23 = "features/generate/service.py::run_ai"


def star_args(name: str, tree: ast.Module) -> set[str]:
    found: set[str] = set()

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNCTIONS):
                label = "<lambda>" if isinstance(child, ast.Lambda) else child.name
                if child.args.vararg or child.args.kwarg:
                    found.add(f"{name}::{where}{label}")
                visit(child, f"{where}{label}.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{where}{child.name}.")
            else:
                visit(child, where)

    visit(tree, "")
    return found


def test_no_function_takes_star_args_or_kwargs():
    found: set[str] = set()
    for name, tree in under("").items():
        found |= star_args(name, tree)
    allowed: set[str] = set()
    for name in found:
        if name == owner_allowed_function_2026_11_23 or name.startswith(
            owner_allowed_file_2026_11_23,
        ):
            allowed.add(name)
    check(found - allowed, set())


def test_the_rule_sees_every_kind_of_function():
    tree = ast.parse(
        dedent("""
        def plain(a, *, b): ...
        def many(*args): ...
        class K:
            async def put(self, **kwargs): ...
        pick = lambda *items: items
        """),
    )

    assert star_args("m.py", tree) == {
        "m.py::many",
        "m.py::K.put",
        "m.py::<lambda>",
    }
