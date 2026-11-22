import ast
import sys
from fnmatch import fnmatch
from pathlib import Path

from import_graph import Edge, graph, module_name, targets
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "tests" / "affected_map.json"
SRC_PREFIX = "src/ofmhelpers/"
MAX_DEPTH = 3
MOST_OF_THE_SUITE = 0.5


class Link(BaseModel):
    when: list[str]
    run: list[str]


class AffectedMap(BaseModel):
    links: list[Link]


def tests_importing(modules: set[str]) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) or (
                isinstance(node, ast.ImportFrom) and not node.level
            ):
                names |= set(targets(node, "", path, modules))
        for name in names:
            found.setdefault(name, set()).add(path.relative_to(ROOT).as_posix())
    return found


def mapped(changed: str, links: AffectedMap) -> set[str]:
    return {
        run
        for link in links.links
        if any(fnmatch(changed, pattern) for pattern in link.when)
        for run in link.run
    }


def own_tests(changed: str) -> set[str]:
    if changed.startswith("tests/") and Path(changed).name.startswith("test_"):
        return {changed}
    if changed.startswith("tests/") and changed.endswith(".py"):
        parent = (ROOT / changed).parent
        return {p.relative_to(ROOT).as_posix() for p in parent.rglob("test_*.py")}
    return set()


def script_tests(changed: str) -> set[str]:
    path = Path(changed)
    if path.parent.as_posix() != "scripts" or path.suffix != ".py":
        return set()
    return {f"tests/unit/scripts/test_{path.stem}.py"}


def dependents(module: str, edges: list[Edge]) -> set[str]:
    reached = {module}
    frontier = {module}
    for _ in range(MAX_DEPTH):
        frontier = {
            e.source for e in edges if e.target in frontier and e.source not in reached
        }
        reached |= frontier
    return reached


def affected(changed_files: list[str]) -> list[str]:
    links = AffectedMap.model_validate_json(MAP.read_text(encoding="utf-8"))
    import_graph = graph()
    modules = set(import_graph.modules)
    importers = tests_importing(modules)
    picked: set[str] = set()
    for changed in changed_files:
        picked |= own_tests(changed) | mapped(changed, links) | script_tests(changed)
        if changed.startswith(SRC_PREFIX) and changed.endswith(".py"):
            for module in dependents(module_name(ROOT / changed), import_graph.edges):
                picked |= importers.get(module, set())
    return sorted(p for p in picked if (ROOT / p).is_file())


def main(arguments: list[str]) -> int:
    picked = affected([a.replace("\\", "/") for a in arguments])
    suite = len(list((ROOT / "tests").rglob("test_*.py")))
    share = len(picked) / suite if suite else 0
    note = f"affected_tests: {len(picked)} of {suite} test files"
    if share > MOST_OF_THE_SUITE:
        note += ", most of the suite: scripts/test.sh alone is as cheap"
    print(note, file=sys.stderr)
    for path in picked:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
