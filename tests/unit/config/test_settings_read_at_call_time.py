"""No value is built from `settings` at import (module level, a class body, a
default argument, a decorator): it would keep what the environment said when
the module loaded. Twelve-Factor "Config"; the convention is in
`ofmhelpers/config/__init__.py`. A `lambda` is fine: it reads when called."""

import ast
from pathlib import Path

SRC = Path(__file__).parents[3] / "src" / "ofmhelpers"
# The app object is built at import, its session middleware with it.
ALLOWED = {"web/main.py"}


class _ReadsSettings(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found = False

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Read when called, not now."""

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name) and node.value.id == "settings":
            self.found = True
        self.generic_visit(node)


def _reads(node: ast.AST) -> bool:
    visitor = _ReadsSettings()
    visitor.visit(node)
    return visitor.found


def _at_import(body: list[ast.stmt]) -> list[int]:
    """The lines of `body` that read `settings` when the module loads."""
    lines = []
    for statement in body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            args = statement.args
            evaluated = [
                *args.defaults,
                *(default for default in args.kw_defaults if default),
                *statement.decorator_list,
            ]
            lines += [statement.lineno for node in evaluated if _reads(node)]
        elif isinstance(statement, ast.ClassDef):
            lines += _at_import(statement.body)
        elif _reads(statement):
            lines.append(statement.lineno)
    return lines


def _found(source: str) -> list[int]:
    return _at_import(ast.parse(source).body)


def test_no_module_keeps_a_setting_from_import():
    found = {
        f"{path.relative_to(SRC).as_posix()}:{line}"
        for path in sorted(SRC.rglob("*.py"))
        if "config" not in path.relative_to(SRC).parts
        for line in _found(path.read_text("utf-8"))
        if path.relative_to(SRC).as_posix() not in ALLOWED
    }
    assert sorted(found) == []


def test_the_check_sees_what_it_is_for():
    source = (
        "ROOT = Path(settings.infra.uploads_root)\n"
        "LATER = lambda: settings.web.gallery_limit\n"
        "class Form:\n"
        "    copies: int = Field(5, le=settings.web.uniqify_max_copies)\n"
        "def cache(ttl_s=settings.infra.cache_ttl_s): ...\n"
        "def fine():\n"
        "    return settings.web.gallery_limit\n"
    )
    assert _found(source) == [1, 4, 5]
