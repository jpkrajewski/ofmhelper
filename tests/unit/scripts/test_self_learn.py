# Copyright (C) 2026 jpkrajewski

import importlib.util
import json
from datetime import UTC, datetime, timedelta
lazy from pathlib import Path

from repo_paths import REPO

_spec = importlib.util.spec_from_file_location(
    "self_learn",
    REPO / "scripts/self_learn.py",
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
            ],
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
                3,
                [("r1", "x" * 5000, False), ("r2", "y", False), ("r3", "z", False)],
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
                9,
                [("c1", "PreToolUse:Bash hook error: run tests in Docker", True)],
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
            },
        )
        + "\n"
        + json.dumps(
            {
                "at": (local - timedelta(hours=3)).isoformat(timespec="seconds"),
                "event": "Stop",
                "tool": "",
                "verdicts": ["review_before_done:block"],
            },
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
        ("Bash: PreToolUse:Bash hook error: run tests in Docker", 2),
    ]
    assert [(b.what, b.count) for b in d.blocks] == [
        ("tests:deny (PreToolUse Bash)", 1),
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
                        {"description": "Fix round", "subagent_type": "x"},
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
                    "content": "<task-notification><task-id>b1</task-id>test.sh: "
                    "pytest exit 0 in 77s\nRESULT GREEN</task-notification>",
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
        ("Review idempotency", 1),
        ("kimi review run", 1),
    ]
    assert (d.task_stops, d.refused_pushes) == (1, 1)
    [cost] = d.costs
    assert cost.peak_context_tokens == 900_000
    report = self_learn.render(d)
    assert "- bbbb2222 deploy: 410s, green" in report
    assert "TaskStop calls: 1" in report
    assert "900,000" in report


def _notification(minute: int, tool_id: str) -> dict:
    return {
        "parentUuid": "p",
        "type": "attachment",
        "timestamp": _at(minute),
        "attachment": {
            "type": "queued_command",
            "prompt": f"<task-notification><task-id>b9</task-id><tool-use-id>{tool_id}</tool-use-id><status>completed</status></task-notification>",  # noqa: E501
        },
    }


def test_runs_that_end_in_a_file_are_paired_with_the_summary_read_later(tmp_path):
    logs = tmp_path / "logs"
    quiet = ' > /dev/null 2>&1; echo "exit=$?"'
    _write(
        logs / "cccc3333-main.jsonl",
        [
            _prompt(1, "run it"),
            _assistant(
                2,
                "m1",
                [
                    ("f1", "Bash", {"command": "scripts/test.sh tests/a.py" + quiet}),
                ],
            ),
            _results(
                5,
                [("f1", "exit=0\nSUMMARY exit 0: 3 passed\nRESULT GREEN", False)],
            ),
            _assistant(
                6,
                "m2",
                [("f2", "Bash", {"command": "scripts/test.sh tests/b.py" + quiet})],
            ),
            _results(6, [("f2", "Command running in background with ID: b9", False)]),
            _notification(16, "f2"),
            _assistant(
                17,
                "m3",
                [("r1", "Bash", {"command": "cat reports/latest/summary.txt"})],
            ),
            _results(17, [("r1", "SUMMARY exit 1: 2 failed\nRESULT NOT GREEN", False)]),
            _assistant(
                18,
                "m4",
                [("f3", "Bash", {"command": "scripts/test.sh tests/c.py" + quiet})],
            ),
            _results(19, [("f3", "", False)]),
            _prompt(20, "it said failed to push and RESULT GREEN, why?"),
            _assistant(
                21,
                "m5",
                [
                    (
                        "a1",
                        "Agent",
                        {
                            "description": "Fix round after review",
                            "subagent_type": "general-purpose",
                        },
                    ),
                    ("g1", "Bash", {"command": "git status"}),
                ],
            ),
            _results(22, [("a1", "done", False), ("g1", "failed to push", False)]),
        ],
    )

    d = self_learn.digest(logs, tmp_path / "none.jsonl", NOW)

    assert [(t.preset, t.seconds, t.result) for t in d.test_runs] == [
        ("files", 180, "green"),
        ("files", 600, "exit 1"),
        ("files", 0, "no result seen"),
    ]
    assert d.reviews == []
    assert d.refused_pushes == 0


def test_a_stray_green_in_other_output_does_not_finish_a_run(tmp_path):
    logs = tmp_path / "logs"
    quiet = ' > /dev/null 2>&1; echo "exit=$?"'
    _write(
        logs / "dddd4444-main.jsonl",
        [
            _prompt(1, "run it"),
            _assistant(
                2,
                "m1",
                [("f1", "Bash", {"command": "scripts/test.sh tests/a.py" + quiet})],
            ),
            _results(3, [("f1", "", False)]),
            _assistant(4, "m2", [("o1", "Bash", {"command": "cat notes.txt"})]),
            _results(5, [("o1", "SUMMARY exit 0: 3 passed\nRESULT GREEN", False)]),
            _assistant(
                6,
                "m3",
                [("s1", "Bash", {"command": "cat reports/latest/summary.txt"})],
            ),
            _results(7, [("s1", "SUMMARY exit 1: 2 failed\nRESULT NOT GREEN", False)]),
            _assistant(
                8,
                "m4",
                [("f2", "Bash", {"command": "scripts/test.sh tests/b.py"})],
            ),
            _results(9, [("f2", "still going", False)]),
        ],
    )

    d = self_learn.digest(logs, tmp_path / "none.jsonl", NOW)

    assert [(t.result) for t in d.test_runs] == ["exit 1", "no result seen"]


def test_kimi_reviews_are_found_behind_timeout_and_the_skill_script(tmp_path):
    logs = tmp_path / "logs"
    commands = [
        "timeout 600 kimi review",
        "python .claude/skills/kimi/ask.py review the diff",
        "cd x && kimi -p review",
        "echo kimi_review",
    ]
    _write(
        logs / "eeee5555-main.jsonl",
        [
            _prompt(1, "go"),
            _assistant(
                2,
                "m1",
                [(f"k{i}", "Bash", {"command": c}) for i, c in enumerate(commands)],
            ),
        ],
    )

    d = self_learn.digest(logs, tmp_path / "none.jsonl", NOW)

    assert sum(r.count for r in d.reviews) == 3


def test_an_attachment_line_without_its_key_is_skipped(tmp_path):
    logs = tmp_path / "logs"
    _write(
        logs / "ffff6666-main.jsonl",
        [
            _prompt(1, "go"),
            {"type": "attachment", "timestamp": _at(2)},
            _assistant(3, "m1", [("g1", "Bash", {"command": "git status"})]),
        ],
    )

    d = self_learn.digest(logs, tmp_path / "none.jsonl", NOW)

    assert d.test_runs == []


def _queued(minute: int, text: str, kind: str) -> dict:
    return {
        "type": "attachment",
        "timestamp": _at(minute),
        "attachment": {
            "type": "queued_command",
            "prompt": text,
            "origin": {"kind": kind},
        },
    }


def test_a_message_typed_while_the_agent_works_counts_as_an_owner_prompt(tmp_path):
    logs = tmp_path / "logs"
    _write(
        logs / "aaaa7777-main.jsonl",
        [
            _prompt(1, "go"),
            _queued(2, "no, use D:/ not C:/", "human"),
            _queued(
                3,
                "<task-notification>done</task-notification>",
                "task-notification",
            ),
        ],
    )

    d = self_learn.digest(logs, tmp_path / "none.jsonl", NOW)

    assert [p.text for p in d.prompts] == ["go", "no, use D:/ not C:/"]


def test_a_queued_record_of_a_wrong_shape_is_skipped(tmp_path):
    logs = tmp_path / "logs"
    wrong = [
        {"type": "attachment", "timestamp": _at(2), "attachment": "just text"},
        _queued(3, ["a", "list"], "human"),
        {
            "type": "attachment",
            "timestamp": _at(4),
            "attachment": {
                "type": "queued_command",
                "prompt": "x",
                "origin": "human",
            },
        },
    ]
    _write(logs / "aaaa8888-main.jsonl", [_prompt(1, "go"), *wrong])

    d = self_learn.digest(logs, tmp_path / "none.jsonl", NOW)

    assert [p.text for p in d.prompts] == ["go"]


def test_done_refuses_an_empty_digest_during_an_open_ship(monkeypatch, tmp_path):
    marker = tmp_path / "last.txt"
    monkeypatch.setattr(self_learn, "MARKER", marker)
    monkeypatch.setattr(self_learn, "OUT", tmp_path)
    monkeypatch.setattr(self_learn, "SHIP_OPEN", tmp_path / "ship.open")
    (tmp_path / "ship.open").write_text("x", encoding="utf-8")
    monkeypatch.setattr(self_learn, "last_pass", lambda: NOW)
    monkeypatch.setattr(
        self_learn,
        "digest",
        lambda *_: self_learn.Digest(
            since="",
            until="",
            prompts=[],
            failures=[],
            blocks=[],
            test_runs=[],
            reviews=[],
            costs=[],
            task_stops=0,
            interrupted=0,
            rejected=0,
            refused_pushes=0,
        ),
    )

    assert self_learn.main(["--done"]) == 1
    assert not marker.exists()


def test_done_accepts_an_explicit_empty_digest_acknowledgement(
    monkeypatch,
    tmp_path,
):
    marker = tmp_path / "last.txt"
    monkeypatch.setattr(self_learn, "MARKER", marker)
    monkeypatch.setattr(self_learn, "OUT", tmp_path)
    monkeypatch.setattr(self_learn, "SHIP_OPEN", tmp_path / "ship.open")
    (tmp_path / "ship.open").write_text("x", encoding="utf-8")

    assert self_learn.main(["--done", "--allow-empty"]) == 0
    assert marker.exists()
