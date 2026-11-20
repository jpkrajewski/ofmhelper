import ast
import re
import sys
from collections import Counter
from functools import cache
from pathlib import Path
from textwrap import dedent

import pytest
from pydantic import BaseModel

SRC = Path(__file__).parents[3] / "src" / "ofmhelpers"
BASELINE = Path(__file__).with_name("baseline.json")

RANK = {
    "web": 4,
    "features": 3,
    "integrations": 2,
    "media": 2,
    "db": 1,
    "cache": 1,
    "schemas": 1,
    "auth": 1,
    "config": 1,
    "utils": 1,
}
ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
MAX_ROUTE_LINES = 25
QUEUE_MACHINERY = ("features/jobs/", "features/events/")
LOOSE = re.compile(
    r"\b(dict|tuple|Dict|Tuple|Mapping|MutableMapping|NamedTuple|TypedDict)\b"
)
SHARED_PARAMETERS = 3
FUNCTION_RULES = (
    "features/",
    "integrations/",
    "schemas/",
    "db/",
    "cache/",
    "media/",
    "utils/",
    "config/",
)
TRACE_CALLS = {"exception", "record_exception"}
LOG_LEVELS = {"debug", "info", "warning", "error", "critical", "log"}
SILENT = "# silent:"


class Baseline(BaseModel):
    layers: set[str]
    router_imports: set[str]
    long_routes: set[str]
    untyped_routes: set[str]
    unrecorded_jobs: set[str]
    loose_returns: set[str]
    repeated_parameters: set[str]
    silent_excepts: set[str]


@cache
def baseline() -> Baseline:
    return Baseline.model_validate_json(BASELINE.read_text("utf-8"))


@cache
def trees() -> dict[str, ast.Module]:
    return {
        path.relative_to(SRC).as_posix(): ast.parse(path.read_text("utf-8"))
        for path in sorted(SRC.rglob("*.py"))
    }


def under(*prefixes: str) -> dict[str, ast.Module]:
    return {name: tree for name, tree in trees().items() if name.startswith(prefixes)}


def imported_areas(tree: ast.Module) -> set[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules += [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
    return {m.split(".")[1] for m in modules if m.startswith("ofmhelpers.")}


def routes(
    tree: ast.Module,
) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]]:
    return [
        (node, decorator)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr in ROUTE_METHODS
    ]


def functions(
    tree: ast.Module,
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found = []
    for node in tree.body:
        members = node.body if isinstance(node, ast.ClassDef) else [node]
        prefix = f"{node.name}." if isinstance(node, ast.ClassDef) else ""
        found += [
            (prefix + member.name, member)
            for member in members
            if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
            and not member.name.startswith("_")
        ]
    return found


class Scope(BaseModel, arbitrary_types_allowed=True):
    aliases: dict[str, ast.expr] = {}
    imported: dict[str, tuple[str, str]] = {}
    modules: dict[str, str] = {}


def module_file(module: str) -> str | None:
    if module != "ofmhelpers" and not module.startswith("ofmhelpers."):
        return None
    path = module.removeprefix("ofmhelpers").lstrip(".").replace(".", "/")
    candidates = [f"{path}.py", f"{path}/__init__.py"] if path else ["__init__.py"]
    return next((name for name in candidates if name in trees()), None)


def imported_from(name: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = ["ofmhelpers", *name.split("/")[:-1]]
    base = package[: len(package) - node.level + 1]
    return ".".join([*base, *([node.module] if node.module else [])])


@cache
def scope(name: str) -> Scope:
    found = Scope()
    for node in trees()[name].body:
        match node:
            case ast.TypeAlias(name=ast.Name(id=alias), value=value):
                found.aliases[alias] = value
            case ast.AnnAssign(target=ast.Name(id=alias), value=ast.expr() as value):
                if "TypeAlias" in ast.unparse(node.annotation):
                    found.aliases[alias] = value
            case ast.Assign(
                targets=[ast.Name(id=alias)],
                value=ast.Name() | ast.Attribute() | ast.Subscript() | ast.BinOp(),
            ):
                found.aliases[alias] = node.value
            case ast.ClassDef(name=alias, bases=[_, *_]):
                found.aliases[alias] = ast.Tuple(elts=list(node.bases), ctx=ast.Load())
            case ast.ImportFrom():
                module = imported_from(name, node)
                for imported in node.names:
                    bound = imported.asname or imported.name
                    if (file := module_file(f"{module}.{imported.name}")) is not None:
                        found.modules[bound] = file
                    elif (file := module_file(module)) is not None:
                        found.imported[bound] = (file, imported.name)
            case ast.Import():
                for imported in node.names:
                    file = module_file(imported.name)
                    if file is not None and imported.asname:
                        found.modules[imported.asname] = file
    return found


def loose_name(alias: str, name: str, seen: frozenset[tuple[str, str]]) -> bool:
    if (name, alias) in seen:
        return False
    seen |= {(name, alias)}
    names = scope(name)
    if alias in names.aliases:
        return loose(names.aliases[alias], name, seen)
    if alias in names.imported:
        file, original = names.imported[alias]
        return loose_name(original, file, seen)
    return False


def loose(
    annotation: ast.expr, name: str, seen: frozenset[tuple[str, str]] = frozenset()
) -> bool:
    if LOOSE.search(ast.unparse(annotation)):
        return True
    for node in ast.walk(annotation):
        match node:
            case ast.Name(id=alias) if loose_name(alias, name, seen):
                return True
            case ast.Attribute(value=ast.Name(id=module), attr=alias):
                file = scope(name).modules.get(module)
                if file is not None and loose_name(alias, file, seen):
                    return True
            case ast.Constant(value=str() as quoted):
                try:
                    inner = ast.parse(quoted, mode="eval").body
                except SyntaxError:
                    continue
                if loose(inner, name, seen):
                    return True
    return False


def queued_function(call: ast.Call) -> ast.expr | None:
    match call.func:
        case ast.Name(id="enqueue") if call.args:
            return call.args[0]
        case ast.Name(id="enqueue_to") if len(call.args) > 1:
            return call.args[1]
        case ast.Attribute(
            attr="put", value=ast.Call(func=ast.Name(id="JobQueue"))
        ) if call.args:
            return call.args[0]
    return None


def leaves_a_trace(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        match node:
            case ast.Raise():
                return True
            case ast.Call(func=ast.Attribute(attr=attr)) if attr in TRACE_CALLS:
                return True
            case ast.Call(func=ast.Attribute(attr=attr), keywords=keywords) if (
                attr in LOG_LEVELS and any(k.arg == "exc_info" for k in keywords)
            ):
                return True
    return False


def quiet(node: ast.AST, lines: list[str]) -> bool:
    match node:
        case ast.ExceptHandler():
            unmarked = not leaves_a_trace(node)
        case ast.With() | ast.AsyncWith():
            unmarked = any(
                isinstance(item.context_expr, ast.Call)
                and ast.unparse(item.context_expr.func).split(".")[-1] == "suppress"
                for item in node.items
            )
        case _:
            return False
    header = lines[node.lineno - 1 : max(node.body[0].lineno - 1, node.lineno)]
    return unmarked and not any(SILENT in line for line in header)


def silent_excepts(name: str, tree: ast.Module, lines: list[str]) -> set[str]:
    found: Counter[str] = Counter()

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, f"{where}.{child.name}" if where else child.name)
                continue
            if quiet(child, lines):
                found[f"{name}::{where or '<module>'}"] += 1
            visit(child, where)

    visit(tree, "")
    return {key if n == 1 else f"{key} x{n}" for key, n in found.items()}


def check(found: set[str], known: set[str]) -> None:
    assert sorted(found - known) == [], (
        "breaks the rule: fix the code, not the baseline"
    )
    assert sorted(known - found) == [], "fixed: delete these lines from baseline.json"


def test_layers_import_downwards_only():
    found = {
        f"{name} -> {area}"
        for name, tree in trees().items()
        if (layer := name.split("/")[0]) in RANK
        for area in imported_areas(tree)
        if RANK.get(area, 0) > RANK[layer]
    }
    check(found, baseline().layers)


def test_routers_import_no_database_and_no_integration():
    found = {
        f"{name} -> {area}"
        for name, tree in under("web/routers/").items()
        for area in imported_areas(tree) & {"db", "integrations"}
    }
    check(found, baseline().router_imports)


def test_routes_are_short():
    found = {
        f"{name}::{route.name}"
        for name, tree in under("web/routers/").items()
        for route, _ in routes(tree)
        if (route.end_lineno or route.lineno) - route.lineno + 1 > MAX_ROUTE_LINES
    }
    check(found, baseline().long_routes)


def test_routes_declare_what_they_return():
    found = {
        f"{name}::{route.name}"
        for name, tree in under("web/routers/").items()
        for route, decorator in routes(tree)
        if route.returns is None
        and "response_model" not in {keyword.arg for keyword in decorator.keywords}
    }
    check(found, baseline().untyped_routes)


def test_background_work_has_a_job_row():
    found = {
        f"{name}::{ast.unparse(queued)}"
        for name, tree in under("web/routers/", "features/").items()
        if not name.startswith(QUEUE_MACHINERY)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (queued := queued_function(node)) is not None
        and ast.unparse(queued) != "run_job"
    }
    check(found, baseline().unrecorded_jobs)


def test_functions_return_models_not_dicts_or_tuples():
    found = {
        f"{name}::{function_name}"
        for name, tree in under(*FUNCTION_RULES).items()
        for function_name, function in functions(tree)
        if function.returns is not None and loose(function.returns, name)
    }
    check(found, baseline().loose_returns)


@pytest.fixture
def files(monkeypatch):
    source = {
        "a.py": """
            from typing import Any, NamedTuple, TypeAlias
            Payload = dict[str, Any]
            type Pair = tuple[int, int]
            Old: TypeAlias = 'Payload'
            class Row(NamedTuple):
                x: int
            class Model:
                pass
        """,
        "b/__init__.py": "",
        "b/c.py": """
            from ofmhelpers.a import Payload as Body, Model
            from ofmhelpers import a
            from ..a import Pair
            Again = Body | None
        """,
    }
    parsed = {name: ast.parse(dedent(text)) for name, text in source.items()}
    monkeypatch.setattr(sys.modules[__name__], "trees", lambda: parsed)
    scope.cache_clear()
    yield
    scope.cache_clear()


@pytest.mark.usefixtures("files")
def test_the_return_rule_follows_a_name_to_the_dict_behind_it():
    def is_loose(written: str, name: str) -> bool:
        return loose(ast.parse(written, mode="eval").body, name)

    here = ["Payload", "Pair", "Old", "Row", "list[Payload]", "'Payload'"]
    imported = ["Body", "Again", "a.Payload", "Pair", "a.Row", "Body | None"]
    models = ["Model", "a.Model", "list[Model]", "Unknown", "a.Unknown"]
    assert [w for w in here if not is_loose(w, "a.py")] == []
    assert [w for w in imported if not is_loose(w, "b/c.py")] == []
    assert [w for w in models if is_loose(w, "b/c.py")] == []


def test_failures_leave_a_trace():
    found = set()
    for name, tree in trees().items():
        lines = (SRC / name).read_text("utf-8").splitlines()
        found |= silent_excepts(name, tree, lines)
    check(found, baseline().silent_excepts)


def test_the_trace_rule_reads_each_handler():
    source = dedent("""
        def traced():
            try: pass
            except A: raise
            try: pass
            except B: logger.exception("x")
            try: pass
            except C as exc: logger.warning("x", exc_info=True)
            try: pass
            except D as exc: span.record_exception(exc)
            try: pass
            except E: return None  # silent: a parser's None
            try: pass
            except (
                F
            ):  # silent: header spread over lines
                pass
        class Box:
            def quiet(self):
                try: pass
                except G: logger.warning("no trace")
                try: pass
                except H: pass
                def inner():
                    with suppress(I): pass
        try: pass
        except J: pass
        with contextlib.suppress(K): pass  # silent: marked
    """)
    found = silent_excepts("m.py", ast.parse(source), source.splitlines())
    assert found == {"m.py::Box.quiet x2", "m.py::Box.quiet.inner", "m.py::<module>"}


def test_functions_sharing_their_parameters_are_one_class():
    found = set()
    for name, tree in under(*FUNCTION_RULES).items():
        leading = Counter(
            tuple(arg.arg for arg in node.args.args[:SHARED_PARAMETERS])
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and len(node.args.args) >= SHARED_PARAMETERS
        )
        found |= {
            f"{name}::({', '.join(names)})"
            for names, count in leading.items()
            if count >= SHARED_PARAMETERS
        }
    check(found, baseline().repeated_parameters)
