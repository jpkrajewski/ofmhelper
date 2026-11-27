# Copyright (C) 2026 jpkrajewski

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
    "background": 4,
    "monitoring": 1,
    "features": 3,
    "integrations": 2,
    "media": 2,
    "db": 1,
    "cache": 1,
    "config": 1,
    "utils": 0,
}
TOP_LEVEL_MODULES = {"acting", "log", "logging", "redact"}
ROUTE_METHODS = {"get", "post", "put", "patch", "delete"}
MAX_ROUTE_LINES = 25
QUEUE_MACHINERY = ("features/jobs/", "features/events/")
LOOSE = re.compile(r"\b(dict|tuple|Dict|Tuple|Mapping|MutableMapping|NamedTuple)\b")
DICTS = re.compile(r"\b(dict|Dict|Mapping|MutableMapping)\b")
SHAPES = re.compile(r"ofmhelpers\.db\.(\w+\.)+(schema|models)$")
SERVES_A_MAPPING = {
    "__getitem__",
    "__contains__",
    "__len__",
    "get",
    "keys",
    "values",
    "items",
    "names",
    "all",
}
SHARED_PARAMETERS = 3
FUNCTION_RULES = (
    "features/",
    "integrations/",
    "db/",
    "cache/",
    "media/",
    "config/",
)
PYDANTIC_HOOKS = {
    "field_validator",
    "model_validator",
    "field_serializer",
    "model_serializer",
}
OPEN_TYPES = {"Any", "object"}
ITERATORS = {
    "Iterator",
    "Iterable",
    "Generator",
    "AsyncIterator",
    "AsyncIterable",
    "AsyncGenerator",
}
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
    root_imports: set[str]


ROOT_MODULES = {"schema.py", "constants.py"}
ROOT_IMPORTS = "constants and schemas must import nothing from the project"


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
def scope(name: str) -> Scope:  # noqa: C901, PLR0912
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
    annotation: ast.expr,
    name: str,
    seen: frozenset[tuple[str, str]] = frozenset(),
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
            attr="put",
            value=ast.Call(func=ast.Name(id="JobQueue")),
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


def test_every_top_level_name_is_ranked_or_a_known_module():
    names = {
        path.stem
        for path in SRC.iterdir()
        if path.name not in {"__init__.py", "__pycache__"}
    }

    assert sorted(names - RANK.keys() - TOP_LEVEL_MODULES) == []


def test_layers_import_downwards_only():
    found = {
        f"{name} -> {area}"
        for name, tree in trees().items()
        if (layer := name.split("/")[0]) in RANK
        for area in imported_areas(tree)
        if RANK.get(area, 0) > RANK[layer]
    }
    check(found, baseline().layers)


def reaches_past_features(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
    return {
        m
        for m in modules
        if m.startswith("ofmhelpers.integrations")
        or (m.startswith("ofmhelpers.db") and not SHAPES.match(m))
    }


def test_web_imports_no_database_and_no_integration():
    found = {
        f"{name} -> {module}"
        for name, tree in under("web/").items()
        for module in reaches_past_features(tree)
    }
    check(found, baseline().router_imports)


def test_the_web_rule_lets_shapes_through_and_nothing_else():
    tree = ast.parse(
        dedent("""
            from ofmhelpers.db.users.schema import SessionUser
            from ofmhelpers.db.devices.catalog.schema import Catalog
            from ofmhelpers.db.users import store
            from ofmhelpers.db.session import session_scope
            from ofmhelpers.integrations.storage.gdrive import connect_as
        """),
    )

    assert reaches_past_features(tree) == {
        "ofmhelpers.db.users",
        "ofmhelpers.db.session",
        "ofmhelpers.integrations.storage.gdrive",
    }


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


def every_function(
    tree: ast.Module,
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found = []

    def visit(node: ast.AST, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                found.append((where + child.name, child))
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                visit(child, f"{where}{child.name}.")
            else:
                visit(child, where)

    visit(tree, "")
    return found


def pydantic_hook(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        ast.unparse(decorator).split("(")[0].split(".")[-1] in PYDANTIC_HOOKS
        for decorator in function.decorator_list
    )


def open_type(annotation: ast.expr) -> bool:
    match annotation:
        case ast.BinOp(op=ast.BitOr(), left=left, right=right):
            return open_type(left) or open_type(right)
        case ast.Name(id=written) | ast.Attribute(attr=written):
            return written in OPEN_TYPES
    return False


def iterates(returns: ast.expr) -> bool:
    outer = returns.value if isinstance(returns, ast.Subscript) else returns
    match outer:
        case ast.Name(id=written) | ast.Attribute(attr=written):
            return written in ITERATORS
    return False


def loose_return(function: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    returns = function.returns
    if returns is None or pydantic_hook(function) or function.name == "__iter__":
        return False
    if iterates(returns):
        return bool(DICTS.search(ast.unparse(returns)))
    return loose(returns, name) or open_type(returns)


def test_every_function_returns_a_model_or_a_plain_value():
    found = {
        f"{name}::{qualified}"
        for name, tree in trees().items()
        for qualified, function in every_function(tree)
        if loose_return(function, name)
    }
    check(found, baseline().loose_returns)


def test_the_return_rule_reads_every_function(monkeypatch):
    tree = ast.parse(
        dedent("""
            from types import MappingProxyType
            from typing import Any, TypedDict
            class Shape(TypedDict):
                x: int
            def plain() -> int: ...
            def shaped() -> Shape: ...
            def lookup() -> MappingProxyType[str, int]: ...
            def _private() -> dict[str, int]: ...
            def pairs() -> Iterator[tuple[int, int]]: ...
            def rows() -> Iterator[dict[str, int]]: ...
            async def outer() -> None:
                def inner() -> tuple[int, int]: ...
            class Box:
                def anything(self) -> Any | None: ...
                def model(self) -> Box: ...
                def __iter__(self) -> object: ...
                @field_validator("x", mode="before")
                def _raw(cls, value: object) -> object: ...
        """),
    )
    monkeypatch.setattr(sys.modules[__name__], "trees", lambda: {"m.py": tree})
    scope.cache_clear()

    flagged = {q for q, f in every_function(tree) if loose_return(f, "m.py")}

    scope.cache_clear()
    assert flagged == {"_private", "rows", "outer.inner", "Box.anything"}


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


def dict_in_disguise(node: ast.ClassDef) -> bool:
    holds = any(
        isinstance(child, ast.AnnAssign) and DICTS.search(ast.unparse(child.annotation))
        for child in node.body
    )
    serves = {
        child.name
        for child in node.body
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    return holds and bool(serves & SERVES_A_MAPPING)


def disguised(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and dict_in_disguise(node)
    }


def test_no_class_is_a_dict_in_disguise():
    found = {f"{name}::{c}" for name, tree in trees().items() for c in disguised(tree)}

    assert sorted(found) == [], (
        "a lookup by name is MappingProxyType[str, T], a fixed shape is a TypedDict"
    )


def test_the_disguise_rule_reads_each_class():
    tree = ast.parse(
        dedent("""
            class Registry(BaseModel):
                entries: dict[str, int]
                def __getitem__(self, name: str) -> int: ...
            class Holder(BaseModel):
                classes: dict[str, int]
            class Shape(TypedDict):
                x: int
            class View(BaseModel):
                rows: list[int]
                def all(self) -> list[int]: ...
        """),
    )

    assert disguised(tree) == {"Registry"}


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


def is_project(name: str) -> bool:
    return name == "ofmhelpers" or name.startswith("ofmhelpers.")


def imports_the_project(node: ast.AST) -> bool:
    if isinstance(node, ast.ImportFrom):
        return node.level > 0 or is_project(node.module or "")
    if isinstance(node, ast.Import):
        return any(is_project(alias.name) for alias in node.names)
    return False


def test_constants_and_schemas_import_nothing_from_the_project():
    found = {
        name
        for name, tree in trees().items()
        if Path(name).name in ROOT_MODULES
        and any(imports_the_project(node) for node in ast.walk(tree))
    }
    known = baseline().root_imports

    assert sorted(found - known) == [], ROOT_IMPORTS
    assert sorted(known - found) == [], "fixed: delete these lines from baseline.json"


def test_the_root_check_sees_a_project_import():
    lines = (
        "from ofmhelpers.config import settings",
        "lazy from ofmhelpers.db.jobs.schema import Job",
        "import ofmhelpers.log",
        "from . import models",
    )
    clean = (
        "from pydantic import BaseModel",
        "import ofmhelpers_tools",
    )

    def found(line: str) -> bool:
        return any(imports_the_project(node) for node in ast.walk(ast.parse(line)))

    assert [line for line in lines if not found(line)] == []
    assert [line for line in clean if found(line)] == []
