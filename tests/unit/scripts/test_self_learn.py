import importlib.util
import json
from datetime import UTC, datetime, timedelta
lazy from pathlib import Path

from repo_paths import REPO

_spec = importlib.util.spec_from_file_location(
    "self_learn", REPO / "scripts/self_learn.py"
)
assert _spec is not None
assert _spec.loader is not None
self_learn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(self_learn)

NOW = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def _at(minute: int) -> str:
    return (NOW + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")


def _assistant(minute: int, reply: str, tools: list[tuple[str, str, dict]]) -> dict:
    return {
        "type": "assistant",
        "timestamp": _at(minute),
        "message": {
            "id": reply,
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 90,
                "cache_read_input_tokens": 1000,
                "output_tokens": 50,
            },
            "content": [
                {"type": "tool_use", "id": i, "name": n, "input": a}
                for i, n, a in tools
            ],
        },
    }


def _results(minute: int, results: list[tuple[str, str, bool]]) -> dict:
    return {
        "type": "user",
        "timestamp": _at(minute),
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": i, "content": c, "is_error": e}
                for i, c, e in results
            ]
        },
    }


def _prompt(minute: int, text: str) -> dict:
    return {
        "type": "user",
        "timestamp": _at(minute),
        "origin": {"kind": "human"},
        "message": {"content": text},
    }


def _write(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '{"type":"last-prompt","leafUuid":"x"}',
        '{"parentUuid":"cut off mid-wri',
    ] + [json.dumps(e, separators=(",", ":")) for e in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_digest_finds_corrections_failures_and_waste(tmp_path):
    logs = tmp_path / "logs"
    read = {"file_path": "C:/repo/src/big.py"}
    _write(
        logs / "aaaa1111-main.jsonl",
        [
            _prompt(-120, "too old to count"),
            _prompt(1, "bro what did i tell you, only the DB and .env"),
            _assistant(2, "m1", [("r1", "Read", read), ("r2", "Read", read)]),
            _assistant(2, "m1", [("r3", "Read", read)]),
            _results(
                3, [("r1", "x" * 5000, False), ("r2", "y", False), ("r3", "z", False)]
            ),
            _assistant(
                4,
                "m2",
                [
                    ("b1", "Bash", {"command": "pytest"}),
                    ("b2", "Bash", {"command": "rm -rf x"}),
                ],
            ),
            _results(
                5,
                [
                    (
                        "b1",
                        "Exit code 1\nPreToolUse:Bash hook error: run tests in Docker",
                        True,
                    ),
                    (
                        "b2",
                        "The user doesn't want to proceed with this tool use.",
                        True,
                    ),
                ],
            ),
            _prompt(6, "[Request interrupted by user for tool use]"),
        ],
    )
    _write(
        logs / "aaaa1111-main" / "subagents" / "agent-x.jsonl",
        [
            _prompt(7, "brief from the main agent"),
            _assistant(8, "s1", [("c1", "Bash", {"command": "pytest"})]),
            _results(
                9, [("c1", "PreToolUse:Bash hook error: run tests in Docker", True)]
            ),
        ],
    )
    audit = tmp_path / "audit.jsonl"
    local = NOW.astimezone().replace(tzinfo=None)
    audit.write_text(
        json.dumps(
            {
                "at": (local + timedelta(minutes=5)).isoformat(timespec="seconds"),
                "event": "PreToolUse",
                "tool": "Bash",
                "verdicts": ["tests:deny"],
            }
        )
        + "\n"
        + json.dumps(
            {
                "at": (local - timedelta(hours=3)).isoformat(timespec="seconds"),
                "event": "Stop",
                "tool": "",
                "verdicts": ["review_before_done:block"],
            }
        )
        + "\n"
        + '{"at": "2026-11-01T1\n',
        encoding="utf-8",
    )

    d = self_learn.digest(logs, audit, NOW)

    assert [p.text for p in d.prompts] == [
        "bro what did i tell you, only the DB and .env",
        "[Request interrupted by user for tool use]",
    ]
    assert (d.interrupted, d.rejected) == (1, 1)
    assert [(f.what, f.count) for f in d.failures] == [
        ("Bash: PreToolUse:Bash hook error: run tests in Docker", 2)
    ]
    assert [(b.what, b.count) for b in d.blocks] == [
        ("tests:deny (PreToolUse Bash)", 1)
    ]
    [cost] = d.costs
    assert (cost.subagents, cost.output_tokens, cost.fresh_input_tokens) == (
        1,
        150,
        300,
    )
    assert [(r.what, r.count) for r in cost.rereads] == [("C:/repo/src/big.py", 3)]
    assert cost.biggest[0].what == "Read C:/repo/src/big.py"
    assert "bro what did i tell you" in self_learn.render(d)


def _usage_assistant(minute: int, reply: str, context: int, tools: list) -> dict:
    entry = _assistant(minute, reply, tools)
    entry["message"]["usage"] = {
        "input_tokens": 1,
        "cache_creation_input_tokens": context - 1,
        "cache_read_input_tokens": 0,
        "output_tokens": 5,
    }
    return entry


def test_the_digest_reports_test_runs_reviews_stops_pushes_and_peak_context(tmp_path):
    logs = tmp_path / "logs"
    _write(
        logs / "bbbb2222-main.jsonl",
        [
            _prompt(1, "ship it"),
            _usage_assistant(
                2,
                "m1",
                50_000,
                [
                    ("t1", "Bash", {"command": "bash scripts/test.sh deploy"}),
                    ("t2", "Bash", {"command": "scripts/test.sh tests/unit/x.py"}),
                    ("t3", "Bash", {"command": "scripts/test.sh"}),
                    ("t4", "Bash", {"command": "scripts/test.sh push"}),
                    (
                        "t5",
                        "Bash",
                        {"command": "echo " + "x" * 200 + " && scripts/test.sh push"},
                    ),
                    ("m1", "Bash", {"command": "cat scripts/test.sh"}),
                    (
                        "a1",
                        "Agent",
                        {
                            "description": "Review idempotency",
                            "subagent_type": "caveman:cavecrew-reviewer",
                        },
                    ),
                    ("k1", "Bash", {"command": "kimi --print review the diff"}),
                ],
            ),
            _results(
                3,
                [
                    (
                        "t1",
                        "test.sh: pytest exit 0 in 410s\nRESULT GREEN",
                        False,
                    ),
                    ("t2", "test.sh: pytest exit 1 in 36s\nRESULT NOT GREEN", True),
                    ("t5", "test.sh: pytest exit 0 in 12s\nRESULT GREEN", False),
                    ("m1", "test.sh: pytest exit 0 in 5s", False),
                    ("t3", "Command running in background with ID: b1", False),
                    ("t4", "Command running in background with ID: b2", False),
                    ("a1", "found 3 issues", False),
                    ("k1", "ok", False),
                ],
            ),
            _usage_assistant(
                4,
                "m2",
                900_000,
                [
                    ("s1", "TaskStop", {"task_id": "b2"}),
                    ("g1", "Bash", {"command": "git push"}),
                    (
                        "a2",
                        "Agent",
                        {"description": "Review idempotency", "subagent_type": "x"},
                    ),
                ],
            ),
            _results(
                5,
                [
                    ("s1", "stopped", False),
                    ("g1", "error: failed to push some refs", True),
                    ("a2", "fine", False),
                ],
            ),
            {
                "type": "user",
                "timestamp": _at(6),
                "message": {
                    "content": "<task-notification><task-id>b1</task-id>test.sh: pytest exit 0 in 77s\nRESULT GREEN</task-notification>"
                },
            },
        ],
    )
    _write(
        logs / "bbbb2222-main" / "subagents" / "agent-r.jsonl",
        [
            _prompt(7, "review brief"),
            _usage_assistant(8, "r1", 2_000_000, [("c1", "Bash", {"command": "ls"})]),
            _results(9, [("c1", "failed to push", True)]),
        ],
    )

    d = self_learn.digest(logs, tmp_path / "none.jsonl", NOW)

    assert [(t.preset, t.seconds, t.result) for t in d.test_runs] == [
        ("deploy", 410, "green"),
        ("files", 36, "exit 1"),
        ("push", 12, "green"),
        ("default", 77, "green"),
        ("push", 0, "no result seen"),
    ]
    assert [(r.what, r.count) for r in d.reviews] == [
        ("Review idempotency", 2),
        ("kimi review run", 1),
    ]
    assert (d.task_stops, d.refused_pushes) == (1, 2)
    [cost] = d.costs
    assert cost.peak_context_tokens == 900_000
    report = self_learn.render(d)
    assert "- bbbb2222 deploy: 410s, green" in report
    assert "TaskStop calls: 1" in report
    assert "900,000" in report
