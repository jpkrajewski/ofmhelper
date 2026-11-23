# Copyright (C) 2026 jpkrajewski

import importlib.util
import re
from types import ModuleType
from typing import Any

from guards.event import ROOT, Guard, HookEvent, Verdict, context, deny
from guards.review import AGENTS, REVIEWER_AGENT, SHELLS, is_review_agent

NAME = "import_graph_before_review"
KIMI_REVIEW = re.compile(r"kimi[/\\]ask\.py\s+review")
SCRIPT = ROOT / "scripts" / "import_graph.py"
LEVELS = ("area", "group")
FIX_FIRST = (
    "CLAUDE.md LAYERS STAY SEPARATE (.claude/rules/layers.md): the import graph "
    "(scripts/import_graph.py) runs before every review and found what the review "
    "must not start on. Fix these first, then start the review again:\n{found}"
)
CLEAN = (
    "Import graph before review (scripts/import_graph.py): {modules} modules, "
    "0 layer breaks, 0 packages importing each other, 0 import cycles."
)
FOUND = (
    "Import graph before review (scripts/import_graph.py) found, report each as a "
    "finding:\n{found}"
)


def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_graph", SCRIPT)
    if spec is None or spec.loader is None:
        msg = f"cannot load {SCRIPT}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def problems(graph: Any) -> list[str]:
    return [
        *(f"layer break: {e.source} -> {e.target}" for e in graph.layer_breaks()),
        *(
            f"packages import each other: {p.first} <-> {p.second}"
            for level in LEVELS
            for p in graph.mutual(level)
        ),
        *(f"import cycle: {' <-> '.join(c)}" for c in graph.cycles(eager_only=True)),
    ]


def is_review_start(event: HookEvent) -> bool:
    if event.hook_event_name == "SubagentStart":
        return event.agent_type == REVIEWER_AGENT
    kimi = event.tool_name in SHELLS and KIMI_REVIEW.search(event.command.lower())
    return is_review_agent(event) or bool(kimi)


def check(event: HookEvent) -> Verdict | None:
    if not is_review_start(event):
        return None
    graph = script().graph()
    found = "\n".join(problems(graph))
    if found and event.hook_event_name == "PreToolUse":
        return deny(NAME, FIX_FIRST.format(found=found))
    if found:
        return context(NAME, FOUND.format(found=found))
    return context(NAME, CLEAN.format(modules=len(graph.modules)))


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse", "SubagentStart"}),
    tools=frozenset({*AGENTS, *SHELLS}),
    check=check,
)
