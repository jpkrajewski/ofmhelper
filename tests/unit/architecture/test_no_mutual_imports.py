# Copyright (C) 2026 jpkrajewski

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "import_graph",
    ROOT / "scripts" / "import_graph.py",
)
assert SPEC is not None
assert SPEC.loader is not None
import_graph = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(import_graph)


def _pairs(level: str) -> list[str]:
    return [
        f"{pair.first} <-> {pair.second}" for pair in import_graph.graph().mutual(level)
    ]


def test_no_two_top_level_packages_import_each_other():
    assert _pairs("area") == []


def test_no_two_packages_import_each_other():
    assert _pairs("group") == []


def test_the_check_sees_two_packages_that_import_each_other():
    edges = [
        import_graph.Edge(source="a.x", target="b.y", lazy=False),
        import_graph.Edge(source="b.z", target="a.w", lazy=True),
        import_graph.Edge(source="c.q", target="a.x", lazy=False),
    ]
    found = import_graph.Graph(modules=["a.x", "a.w", "b.y", "b.z", "c.q"], edges=edges)

    assert [(p.first, p.second) for p in found.mutual("area")] == [("a", "b")]


BOTTOM_UP = (
    {"config", "redact", "acting"},
    {"log"},
    {"cache", "monitoring"},
    {"db", "media"},
    {"integrations"},
    {"features"},
    {"web", "background"},
)
LEVEL = {area: level for level, areas in enumerate(BOTTOM_UP) for area in areas}


def test_every_area_imports_only_areas_strictly_below_it():
    found = {
        f"{c.source} -> {c.target}"
        for c in import_graph.graph().counts("area")
        if LEVEL.get(c.target, len(BOTTOM_UP)) >= LEVEL.get(c.source, -1)
    }

    assert sorted(found) == []


def test_no_import_cycle_among_the_imports_that_run_at_load():
    assert import_graph.graph().cycles(eager_only=True) == []
