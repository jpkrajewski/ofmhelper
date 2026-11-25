# noqa: CPY001

import ast
import json
import math
import sys
from pathlib import Path


class CallableCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: list[str] = []
        self.class_names: list[str] = []
        self.records: list[dict] = []
        self.classes: list[dict] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.classes.append(
            {
                "name": ".".join([*self.names, node.name]),
                "simple_name": node.name,
                "lineno": node.lineno,
                "endline": node.end_lineno,
            },
        )
        self.names.append(node.name)
        self.class_names.append(node.name)
        self.generic_visit(node)
        self.class_names.pop()
        self.names.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.collect_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.collect_function(node)

    def collect_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        kind = (
            "method"
            if self.class_names and self.class_names[-1] == self.names[-1]
            else "function"
        )
        self.names.append(node.name)
        self.records.append(
            {
                "name": ".".join(self.names),
                "simple_name": node.name,
                "kind": kind,
                "lineno": node.lineno,
                "endline": node.end_lineno,
                "max_nesting_depth": body_depth(node.body, 0),
            },
        )
        self.generic_visit(node)
        self.names.pop()


def body_depth(statements: list[ast.stmt], depth: int) -> int:
    maximum = depth
    for statement in statements:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        maximum = max(maximum, statement_depth(statement, depth))
    return maximum


def statement_depth(statement: ast.stmt, depth: int) -> int:
    control_flow = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.TryStar,
        ast.With,
        ast.AsyncWith,
        ast.Match,
    )
    if not isinstance(statement, control_flow):
        return depth
    nested_depth = depth + 1
    if isinstance(statement, ast.If):
        elif_depth = (
            depth
            if len(statement.orelse) == 1
            and isinstance(statement.orelse[0], ast.If)
            and statement.orelse[0].col_offset == statement.col_offset
            else nested_depth
        )
        return max(
            nested_depth,
            body_depth(statement.body, nested_depth),
            body_depth(statement.orelse, elif_depth),
        )
    if isinstance(statement, ast.For | ast.AsyncFor | ast.While):
        return max(
            nested_depth,
            body_depth(statement.body, nested_depth),
            body_depth(statement.orelse, nested_depth),
        )
    if isinstance(statement, ast.Try | ast.TryStar):
        handler_depths = []
        for handler in statement.handlers:
            handler_depths.append(body_depth(handler.body, nested_depth))  # noqa: PERF401
        return max(
            nested_depth,
            *handler_depths,
            body_depth(statement.body, nested_depth),
            body_depth(statement.orelse, nested_depth),
            body_depth(statement.finalbody, nested_depth),
        )
    if isinstance(statement, ast.With | ast.AsyncWith):
        return body_depth(statement.body, nested_depth)
    case_depths = []
    for case in statement.cases:
        case_depths.append(body_depth(case.body, nested_depth))  # noqa: PERF401
    return max(nested_depth, *case_depths)


def flatten_blocks(blocks: list[dict]) -> list[dict]:
    found = []
    for block in blocks:
        found.append(block)
        found.extend(flatten_blocks(block.get("methods", [])))
        found.extend(flatten_blocks(block.get("closures", [])))
    return found


def parse_source(path: Path, root: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as error:
        relative = path.relative_to(root).as_posix()
        msg = f"{relative}: {error}"
        raise ValueError(msg) from error


def source_paths(root: Path) -> list[Path]:
    return sorted(
        path
        for area in ("src", "tests", "alembic")
        for path in (root / area).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def callable_metrics(root: Path, radon: dict) -> tuple[list[dict], list[dict]]:
    callables = []
    classes = []
    for source_path in source_paths(root):
        relative = source_path.relative_to(root).as_posix()
        blocks = radon.get(relative, [])
        if isinstance(blocks, dict) and "error" in blocks:
            msg = f"{relative}: {blocks['error']}"
            raise ValueError(msg)
        if not isinstance(blocks, list):
            msg = f"{relative}: invalid Radon complexity data"
            raise TypeError(msg)
        by_identity = {
            (block["lineno"], block["name"]): block for block in flatten_blocks(blocks)
        }
        collector = CallableCollector()
        collector.visit(parse_source(source_path, root))
        for record in collector.records:
            identity = (record["lineno"], record["simple_name"])
            block = by_identity.get(identity)
            if block is None:
                lineno = record["lineno"]
                endline = record["endline"]
                complexity = None
                density = None
            else:
                lineno = block["lineno"]
                endline = block["endline"]
                if endline == lineno and record["endline"] > lineno:
                    endline = record["endline"]
                complexity = block["complexity"]
                density = round(
                    100 * complexity / max(1, endline - lineno + 1),
                    3,
                )
            callables.append(
                {
                    "path": relative,
                    "name": record["name"],
                    "kind": record["kind"],
                    "lineno": lineno,
                    "endline": endline,
                    "line_span": endline - lineno + 1,
                    "complexity": complexity,
                    "complexity_per_100_lines": density,
                    "max_nesting_depth": record["max_nesting_depth"],
                },
            )
        for record in collector.classes:
            block = by_identity.get((record["lineno"], record["simple_name"]))
            lineno = block["lineno"] if block else record["lineno"]
            endline = block["endline"] if block else record["endline"]
            classes.append(
                {
                    "path": relative,
                    "name": record["name"],
                    "lineno": lineno,
                    "endline": endline,
                    "line_span": endline - lineno + 1,
                    "complexity": block["complexity"] if block else None,
                },
            )
    callables.sort(key=lambda record: (record["path"], record["lineno"]))
    classes.sort(key=lambda record: (record["path"], record["lineno"]))
    return callables, classes


def module_name(path: Path, root: Path) -> str:
    relative = path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    if parts[0] == "src":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def import_base(node: ast.ImportFrom, source: str, source_path: Path) -> str:
    if not node.level:
        return node.module or ""
    package = source if source_path.name == "__init__.py" else source.rpartition(".")[0]
    package_parts = package.split(".") if package else []
    keep = len(package_parts) - node.level + 1
    base_parts = package_parts[: max(0, keep)]
    if node.module:
        base_parts.extend(node.module.split("."))
    return ".".join(base_parts)


def internal_module(candidate: str, source: str, modules: set[str]) -> str | None:
    if candidate in modules:
        return candidate
    area = source.split(".", maxsplit=1)[0]
    area_candidate = f"{area}.{candidate}"
    if area_candidate in modules:
        return area_candidate
    return None


def import_targets(
    node: ast.Import | ast.ImportFrom,
    source: str,
    source_path: Path,
    modules: set[str],
) -> set[str]:
    targets = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            target = internal_module(alias.name, source, modules)
            if target is not None and target != source:
                targets.add(target)
        return targets
    base = import_base(node, source, source_path)
    for alias in node.names:
        qualified = f"{base}.{alias.name}" if base and alias.name != "*" else base
        target = internal_module(qualified, source, modules)
        if target is None:
            target = internal_module(base, source, modules)
        if target is not None and target != source:
            targets.add(target)
    return targets


def dependency_entropy(targets: set[str]) -> float:
    count = len(targets)
    if count <= 1:
        return 0
    probability = 1 / count
    return -sum(probability * math.log2(probability) for _target in targets)


def dependency_metrics(root: Path) -> dict:
    paths = {module_name(path, root): path for path in source_paths(root)}
    modules = set(paths)
    outgoing = {module: set() for module in modules}
    for source, source_path in paths.items():
        for node in ast.walk(parse_source(source_path, root)):
            if isinstance(node, ast.Import | ast.ImportFrom):
                outgoing[source].update(
                    import_targets(node, source, source_path, modules),
                )
    module_records = []
    area_targets = {area: set() for area in ("ofmhelpers", "tests", "alembic")}
    for source in sorted(modules):
        targets = outgoing[source]
        module_records.append(
            {
                "module": source,
                "path": paths[source].relative_to(root).as_posix(),
                "targets": sorted(targets),
                "dependency_count": len(targets),
                "entropy_bits": dependency_entropy(targets),
            },
        )
        area_targets[source.split(".", maxsplit=1)[0]].update(targets)
    area_records = []
    for area, targets in area_targets.items():
        area_records.append(
            {
                "area": area,
                "targets": sorted(targets),
                "dependency_count": len(targets),
                "entropy_bits": dependency_entropy(targets),
            },
        )
    return {"modules": module_records, "areas": area_records}


def ranked_offenders(
    callables: list[dict],
    classes: list[dict],
    maintainability: list[dict],
) -> dict:
    blocks = [*callables, *classes]
    complexity_blocks = []
    density_blocks = []
    for record in callables:
        if record["complexity"] is not None:
            complexity_blocks.append(record)
        if record["complexity_per_100_lines"] is not None:
            density_blocks.append(record)
    return {
        "complexity": sorted(
            complexity_blocks,
            key=lambda record: (
                -record["complexity"],
                record["path"],
                record["lineno"],
            ),
        )[:20],
        "block_size": sorted(
            blocks,
            key=lambda record: (-record["line_span"], record["path"], record["lineno"]),
        )[:20],
        "density": sorted(
            density_blocks,
            key=lambda record: (
                -record["complexity_per_100_lines"],
                record["path"],
                record["lineno"],
            ),
        )[:20],
        "nesting": sorted(
            callables,
            key=lambda record: (
                -record["max_nesting_depth"],
                record["path"],
                record["lineno"],
            ),
        )[:20],
        "maintainability": sorted(
            maintainability,
            key=lambda record: (record["mi"], record["path"]),
        )[:20],
    }


def write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def combined_results(work: Path) -> dict:
    definitions = (
        ("radon_cc", (0,), "json"),
        ("radon_raw", (0,), "json"),
        ("radon_mi", (0,), "json"),
        ("xenon", (0,), "text"),
        ("pylint", (0, 8), "json"),
        ("import_linter", (0,), "text"),
        ("custom_metrics", (0,), "custom"),
    )
    tools = {}
    for name, accepted, kind in definitions:
        status_path = work / f"{name}.status"
        try:
            exit_code = int(status_path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            exit_code = None
        passed = exit_code in accepted
        result = {"exit_code": exit_code, "passed": passed}
        if kind == "text":
            result["output"] = (work / f"{name}.log").read_text(encoding="utf-8")
        else:
            filename = "custom-metrics" if kind == "custom" else name
            try:
                result["results"] = json.loads(
                    (work / f"{filename}.json").read_text(encoding="utf-8"),
                )
            except (OSError, json.JSONDecodeError) as error:
                result["passed"] = False
                result["error"] = str(error)
            if not result["passed"]:
                result["output"] = (work / f"{name}.log").read_text(encoding="utf-8")
        if name == "xenon":
            result["limits"] = {
                "max_absolute": "F",
                "max_modules": "C",
                "max_average": "A",
            }
        tools[name] = result
    summary_valid = True
    try:
        summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        summary = {"error": str(error)}
        summary_valid = False
    return {
        "passed": all(result["passed"] for result in tools.values()) and summary_valid,
        "tools": tools,
        "summary": summary,
    }


def main(arguments: list[str]) -> int:
    if arguments[:1] == ["combine"]:
        if not arguments[1:] or arguments[2:]:
            msg = "usage: quality_metrics.py combine WORK_DIRECTORY"
            raise ValueError(msg)
        report = combined_results(Path(arguments[1]))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1
    if len(arguments) not in (3, 4):
        msg = (
            "usage: quality_metrics.py REPOSITORY_ROOT RADON_CC_JSON "
            "OUTPUT_DIRECTORY [RADON_MI_JSON]"
        )
        raise ValueError(msg)
    root = Path(arguments[0]).resolve()
    radon_path = Path(arguments[1])
    output = Path(arguments[2])
    radon = json.loads(radon_path.read_text(encoding="utf-8"))
    callables, classes = callable_metrics(root, radon)
    dependencies = dependency_metrics(root)
    maintainability = []
    if arguments[3:]:
        mi_data = json.loads(Path(arguments[3]).read_text(encoding="utf-8"))
        for path, values in mi_data.items():
            maintainability.append(
                {"path": path, "mi": values["mi"], "rank": values["rank"]},
            )
    maintainability.sort(key=lambda record: record["path"])
    offenders = ranked_offenders(callables, classes, maintainability)
    output.mkdir(parents=True, exist_ok=True)
    write_json(
        output / "custom-metrics.json",
        {
            "callables": callables,
            "classes": classes,
            "dependencies": dependencies,
            "maintainability": maintainability,
        },
    )
    write_json(
        output / "summary.json",
        {
            "counts": {
                "files": len(dependencies["modules"]),
                "callables": len(callables),
                "classes": len(classes),
            },
            "offenders": offenders,
            "dependency_areas": dependencies["areas"],
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
