# Copyright (C) 2026 jpkrajewski

import ast
import json
import sys
from collections import Counter
from html import escape
from pathlib import Path

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ofmhelpers"
PACKAGE = "ofmhelpers"
OUT = ROOT / "reports" / "deps"
LAYERS = {
    "web": 4,
    "background": 4,
    "features": 3,
    "integrations": 2,
    "media": 2,
    "db": 1,
    "cache": 1,
    "monitoring": 1,
    "config": 1,
}
TOP = 10
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"


class Edge(BaseModel, frozen=True):
    source: str
    target: str
    lazy: bool


class Count(BaseModel, frozen=True):
    source: str
    target: str
    imports: int
    lazy: int


class Ranked(BaseModel, frozen=True):
    module: str
    imports: int


class Pair(BaseModel, frozen=True):
    first: str
    second: str


class Graph(BaseModel):
    modules: list[str]
    edges: list[Edge]

    def area_of(self, module: str) -> str:
        return module.split(".", maxsplit=1)[0]

    def group_of(self, module: str) -> str:
        return ".".join(module.split(".")[:2])

    def counts(self, level: str) -> list[Count]:
        name = self.area_of if level == "area" else self.group_of
        total: Counter[tuple[str, str]] = Counter()
        lazy: Counter[tuple[str, str]] = Counter()
        for edge in self.edges:
            pair = (name(edge.source), name(edge.target))
            if pair[0] != pair[1]:
                total[pair] += 1
                lazy[pair] += edge.lazy
        return [
            Count(source=s, target=t, imports=n, lazy=lazy[(s, t)])
            for (s, t), n in sorted(total.items(), key=lambda item: -item[1])
        ]

    def mutual(self, level: str) -> list[Pair]:
        pairs = {(c.source, c.target) for c in self.counts(level)}
        return [
            Pair(first=a, second=b)
            for a, b in sorted(pairs)
            if a < b and (b, a) in pairs
        ]

    def layer_breaks(self) -> list[Edge]:
        return [
            edge
            for edge in self.edges
            if LAYERS.get(self.area_of(edge.target), 0)
            > LAYERS.get(self.area_of(edge.source), 99)
        ]

    def cycles(self, *, eager_only: bool) -> list[list[str]]:
        follows: dict[str, list[str]] = {m: [] for m in self.modules}
        for edge in self.edges:
            if not (eager_only and edge.lazy):
                follows[edge.source].append(edge.target)
        return [c for c in strongly_connected(follows) if len(c) > 1]

    def ranked(self, *, incoming: bool) -> list[Ranked]:
        counted = Counter(e.target if incoming else e.source for e in self.edges)
        return [Ranked(module=m, imports=n) for m, n in counted.most_common(TOP)]

    def of(self, module: str) -> list[Edge]:
        return [e for e in self.edges if module in (e.source, e.target)]


def strongly_connected(follows: dict[str, list[str]]) -> list[list[str]]:  # noqa: C901
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    for start, first in follows.items():
        if start in index:
            continue
        work = [(start, iter(first))]
        index[start] = low[start] = len(index)
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is not None:
                if child not in index:
                    index[child] = low[child] = len(index)
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(follows[child])))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                found.append(sorted(component))
    return found


def module_name(path: Path) -> str:
    parts = list(path.relative_to(SRC).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "__init__"


def is_package(path: Path) -> bool:
    return path.name == "__init__.py"


def base_of(module: str, path: Path, level: int) -> str:
    parts = module.split(".") if module != "__init__" else []
    package = parts if is_package(path) else parts[:-1]
    return ".".join(package[: len(package) - level + 1])


def targets(
    node: ast.Import | ast.ImportFrom,
    module: str,
    path: Path,
    modules: set[str],
) -> list[str]:
    if isinstance(node, ast.Import):
        written = [alias.name for alias in node.names]
    else:
        base = base_of(module, path, node.level) if node.level else ""
        prefix = ".".join(
            filter(None, [PACKAGE if node.level else "", base, node.module or ""]),
        )
        written = [
            f"{prefix}.{alias.name}"
            if f"{prefix}.{alias.name}".removeprefix(f"{PACKAGE}.") in modules
            else prefix
            for alias in node.names
        ]
    inside = [
        name.removeprefix(f"{PACKAGE}.")
        for name in written
        if name.startswith(f"{PACKAGE}.")
    ]
    return sorted({name for name in inside if name in modules} - {module})


def graph() -> Graph:
    paths = sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)
    modules = {module_name(p): p for p in paths}
    edges: set[Edge] = set()
    for module, path in modules.items():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import | ast.ImportFrom):
                lazy = bool(node.is_lazy)
                edges |= {
                    Edge(source=module, target=target, lazy=lazy)
                    for target in targets(node, module, path, set(modules))
                }
    eager = {(e.source, e.target) for e in edges if not e.lazy}
    kept = [e for e in edges if not e.lazy or (e.source, e.target) not in eager]
    return Graph(
        modules=sorted(modules),
        edges=sorted(kept, key=lambda e: (e.source, e.target)),
    )


def summary(found: Graph) -> str:
    lazy = sum(e.lazy for e in found.edges)
    breaks = found.layer_breaks()
    eager_cycles = found.cycles(eager_only=True)
    all_cycles = found.cycles(eager_only=False)
    lines = [
        f"MODULES {len(found.modules)}, IMPORTS {len(found.edges)} ({lazy} lazy)",
        "AREAS (who imports whom, lazy in brackets):",
        *(
            f"  {c.source:>13} -> {c.target:<13} {c.imports:5} ({c.lazy})"
            for c in found.counts("area")
        ),
        f"LAYER BREAKS (an area importing one above it): {len(breaks)}",
        *(f"  {e.source} -> {e.target}" for e in breaks),
        *(
            f"PACKAGES IMPORTING EACH OTHER ({level}): {len(found.mutual(level))}"
            for level in ("area", "group")
        ),
        *(
            f"  {pair.first} <-> {pair.second}"
            for level in ("area", "group")
            for pair in found.mutual(level)
        ),
        f"CYCLES (plain imports, a real problem): {len(eager_cycles)}",
        *(f"  {' <-> '.join(c)}" for c in eager_cycles),
        f"CYCLES only through lazy imports: {len(all_cycles) - len(eager_cycles)}",
        f"MOST IMPORTED (top {TOP}):",
        *(f"  {r.imports:4} {r.module}" for r in found.ranked(incoming=True)),
        f"IMPORTS MOST (top {TOP}):",
        *(f"  {r.imports:4} {r.module}" for r in found.ranked(incoming=False)),
    ]
    return "\n".join(lines)


def one_module(found: Graph, module: str) -> str:
    edges = found.of(module)
    if module not in found.modules:
        return f"no module {module!r} (names are dotted, without 'ofmhelpers.')"
    imports = [
        f"  -> {e.target}{' (lazy)' if e.lazy else ''}"
        for e in edges
        if e.source == module
    ]
    importers = [
        f"  <- {e.source}{' (lazy)' if e.lazy else ''}"
        for e in edges
        if e.target == module
    ]
    return "\n".join(
        [
            f"{module} imports {len(imports)}:",
            *imports,
            f"imported by {len(importers)}:",
            *importers,
        ],
    )


def mermaid(found: Graph) -> str:
    lines = ["graph LR"]
    for count in found.counts("area"):
        style = "-.->" if count.lazy == count.imports else "-->"
        lines.append(f"  {count.source} {style}|{count.imports}| {count.target}")
    return "\n".join(lines)


def rows(counts: list[Count]) -> str:
    return "".join(
        f"<tr><td>{escape(c.source)}</td><td>{escape(c.target)}</td>"
        f"<td>{c.imports}</td><td>{c.lazy}</td></tr>"
        for c in counts
    )


def page(found: Graph) -> str:
    edges = json.dumps([e.model_dump() for e in found.edges])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Import graph</title>
<style>
body{{font:14px system-ui;margin:16px;max-width:1100px}}
table{{border-collapse:collapse;width:100%;margin:8px 0 24px}}
td,th{{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left}}
th{{position:sticky;top:0;background:#fff}}
input{{font:inherit;padding:6px;width:100%;box-sizing:border-box}}
pre{{background:#f6f6f6;padding:12px;overflow:auto}}
</style></head><body>
<h1>Import graph of src/ofmhelpers</h1>
<pre>{escape(summary(found))}</pre>
<h2>Areas</h2>
<pre class="mermaid">{escape(mermaid(found))}</pre>
<h2>Packages</h2>
<table><tr><th>imports from</th><th>imports</th><th>count</th><th>lazy</th></tr>
{rows(found.counts("group"))}</table>
<h2>One module</h2>
<input id="find" placeholder="module, e.g. features.trials.folder">
<pre id="one"></pre>
<script src="{MERMAID}"></script>
<script>
mermaid.initialize({{startOnLoad: true}});
const edges = {edges};
document.getElementById("find").addEventListener("input", (event) => {{
  const name = event.target.value.trim();
  const out = edges.filter((e) => e.source === name).map((e) => "-> "
      "" + e.target + (e.lazy ? " (lazy)" : ""));
  const into = edges.filter((e) => e.target === name).map((e) => "<- "
      "" + e.source + (e.lazy ? " (lazy)" : ""));
  document.getElementById("one").textContent = [name + " imports "
      "" + out.length, ...out, "imported by " + into.length, ...into].join("\\n");
}});
</script></body></html>
"""


def main(arguments: list[str]) -> int:
    found = graph()
    if arguments:
        print(one_module(found, arguments[0]))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(page(found), encoding="utf-8")
    print(summary(found))
    print(f"page: {(OUT / 'index.html').relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
