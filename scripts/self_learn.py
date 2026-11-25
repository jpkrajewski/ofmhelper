# Copyright (C) 2026 jpkrajewski

import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
lazy from collections.abc import Iterable, Iterator

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "self-learn"
MARKER = OUT / "last.txt"
SHIP_OPEN = ROOT / "reports" / "hooks" / "ship.open"
AUDIT = ROOT / "reports" / "hooks" / "audit.jsonl"
DEFAULT_DAYS = 7
METADATA_LINE = '{"type":"'
KEPT_TYPES = ('{"type":"user"', '{"type":"assistant"', '{"type":"attachment"')
INTERRUPTED = "[Request interrupted by user"
REJECTED = "The user doesn't want to proceed with this tool use"
CONTINUED = "This session is being continued from a previous conversation"
REREAD_MIN = 3
TOP = 15
BIGGEST = 3
TEXT_MAX = 400
WHAT_MAX = 140
TEST_SH = re.compile(
    r"(?:^|[;&|(\n])\s*(?:\w+=\S+\s+)*(?:(?:bash|sh)\s+)?(?:\S*/)?scripts/test\.sh(?:\s+([^\s|;&>]+))?",
)
BACKGROUND_ID = re.compile(r"running in background with ID: (\w+)")
TASK_ID = re.compile(r"<task-id>(\w+)</task-id>")
TOOL_USE_ID = re.compile(r"<tool-use-id>(\w+)</tool-use-id>")
TEST_EXIT = re.compile(r"test\.sh: pytest exit (\d+) in (\d+)s")
SUMMARY_EXIT = re.compile(r"SUMMARY exit (\d+):")
EXIT_ECHO = re.compile(r"\bexit=(\d+)")
GIT_PUSH = re.compile(r"\bgit\s+push\b")
KIMI_RUN = re.compile(
    r"kimi[/\\]ask\.py"
    r"|(?:^|[;&|(\n])\s*(?:\w+=\S+\s+)*(?:(?:timeout|nohup|time|env)\s+(?:-\S+\s+|\d+[smhd]?\s+)*)?"
    r"(?:\S*[/\\])?kimi(?:\.exe)?(?=\s|$)",
)
SUMMARY_FILE = "reports/latest/summary.txt"
QUIET_RUN = re.compile(r">\s*(?!&)\S")
REVIEWER_AGENT = "caveman:cavecrew-reviewer"
PRESETS = frozenset(
    {
        "e2e",
        "all",
        "push",
        "live",
        "deploy",
        "paid",
        "snapshot",
        "durations",
        "affected",
    },
)
SUBAGENT_TOOLS = frozenset({"Agent", "Task"})
REFUSED_PUSH = "failed to push"
NO_RESULT = "no result seen"


class Correction(BaseModel):
    at: str
    session: str
    text: str


class Repeat(BaseModel):
    what: str
    count: int


class TestShRun(BaseModel):
    session: str
    preset: str
    seconds: int
    result: str


class SessionCost(BaseModel):
    session: str
    started: str
    minutes: int
    subagents: int
    output_tokens: int
    fresh_input_tokens: int
    cache_read_tokens: int
    peak_context_tokens: int = 0
    rereads: list[Repeat]
    biggest: list[Repeat]


class SessionLog(BaseModel):
    cost: SessionCost
    prompts: list[Correction]
    interrupted: int
    rejected: int
    errors: list[str]
    test_runs: list[TestShRun] = []
    reviews: list[Repeat] = []
    task_stops: int = 0
    refused_pushes: int = 0


class Digest(BaseModel):
    since: str
    until: str
    prompts: list[Correction]
    interrupted: int
    rejected: int
    failures: list[Repeat]
    blocks: list[Repeat]
    costs: list[SessionCost]
    test_runs: list[TestShRun] = []
    reviews: list[Repeat] = []
    task_stops: int = 0
    refused_pushes: int = 0


def project_logs() -> Path:
    return (
        Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))
    )


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def _entries(path: Path, since: datetime) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if line.startswith(METADATA_LINE) and not line.startswith(KEPT_TYPES):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                print(f"self_learn: skipped a broken line in {path}", file=sys.stderr)
                continue
            stamp = entry.get("timestamp")
            if stamp and _when(stamp) >= since:
                yield entry


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or part.get("content") or "")
            for part in content
            if isinstance(part, dict)
        )
    return ""


def _first_line(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("Exit code"):
            return line[:WHAT_MAX]
    return text.strip()[:WHAT_MAX]


def _tool_results(content: object) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [
        p for p in content if isinstance(p, dict) and p.get("type") == "tool_result"
    ]


def _target(tool_input: dict[str, Any]) -> str:
    raw = (
        tool_input.get("file_path")
        or tool_input.get("command")
        or tool_input.get("query")
    )
    return str(raw or "")[:WHAT_MAX].replace("\n", " ")


def _test_preset(command: str) -> str | None:
    found = TEST_SH.search(command)
    if not found:
        return None
    word = found.group(1)
    if word is None:
        return "default"
    return word if word in PRESETS else "files"


def _review_label(name: str, tool_input: dict[str, Any]) -> str | None:
    if name in SUBAGENT_TOOLS and tool_input.get("subagent_type") == REVIEWER_AGENT:
        return str(tool_input.get("description") or REVIEWER_AGENT)
    command = str(tool_input.get("command", ""))
    if name == "Bash" and KIMI_RUN.search(command) and "review" in command.lower():
        return "kimi review run"
    return None


def _test_verdict(text: str, *, own: bool) -> tuple[int, int | None] | None:
    found = TEST_EXIT.search(text)
    code, seconds = (int(found.group(1)), int(found.group(2))) if found else (0, None)
    if not found:
        summary = SUMMARY_EXIT.search(text) or (own and EXIT_ECHO.search(text))
        if summary:
            code = int(summary.group(1))
        elif "RESULT NOT GREEN" in text:
            code = 1
        elif "RESULT GREEN" not in text:
            return None
    if "RESULT NOT GREEN" in text and code == 0:
        code = 1
    return code, seconds


class TestRuns:
    __test__ = False

    def __init__(self, session: str) -> None:
        self.session = session
        self.pending: dict[str, tuple[str, datetime]] = {}
        self.background: dict[str, str] = {}
        self.completed: set[str] = set()
        self.summary_reads: set[str] = set()
        self.quiet: set[str] = set()
        self.ended: dict[str, datetime] = {}
        self.done: list[TestShRun] = []

    def launch(self, tool_id: str, preset: str, stamp: str) -> None:
        self.pending[tool_id] = (preset, _when(stamp))

    def read_summary_file(self, tool_id: str, command: str) -> None:
        if SUMMARY_FILE in command:
            self.summary_reads.add(tool_id)

    def run_without_output(self, tool_id: str, command: str) -> None:
        if tool_id in self.pending and QUIET_RUN.search(command):
            self.quiet.add(tool_id)

    def answer(self, key: str, text: str, stamp: str) -> None:
        tool_id = self.background.get(key, key)
        if tool_id not in self.pending:
            if tool_id in self.summary_reads:
                self._read_summary(text, stamp)
        elif started := BACKGROUND_ID.search(text):
            self.background[started.group(1)] = tool_id
        else:
            verdict = _test_verdict(text, own=True)
            if verdict is not None or tool_id in self.quiet:
                self.completed.add(tool_id)
            self._finish(tool_id, verdict, stamp)

    def notified(self, text: str, stamp: str) -> None:
        found = TOOL_USE_ID.search(text)
        if found and found.group(1) in self.pending:
            self.completed.add(found.group(1))
            self.ended[found.group(1)] = _when(stamp)

    def _read_summary(self, text: str, stamp: str) -> None:
        verdict = _test_verdict(text, own=False)
        oldest = next((k for k in self.pending if k in self.completed), None)
        if oldest:
            self._finish(oldest, verdict, stamp)

    def _finish(
        self,
        tool_id: str,
        verdict: tuple[int, int | None] | None,
        stamp: str,
    ) -> None:
        if verdict is None:
            return
        code, seconds = verdict
        preset, launched = self.pending.pop(tool_id)
        end = self.ended.get(tool_id) or _when(stamp)
        self.done.append(
            TestShRun(
                session=self.session,
                preset=preset,
                seconds=seconds
                if seconds is not None
                else max(0, int((end - launched).total_seconds())),
                result="green" if code == 0 else f"exit {code}",
            ),
        )

    def all(self) -> list[TestShRun]:
        return self.done + [
            TestShRun(session=self.session, preset=p, seconds=0, result=NO_RESULT)
            for p, _ in self.pending.values()
        ]


def _notified_task(text: str) -> str:
    found = TASK_ID.search(text)
    return found.group(1) if found else ""


def _is_owner_prompt(entry: dict[str, Any], text: str) -> bool:
    origin = entry.get("origin") or {}
    return (
        origin.get("kind") == "human"
        and not entry.get("isMeta")
        and bool(text.strip())
        and not text.startswith(CONTINUED)
    )


def _is_queued_owner_prompt(attached: Any) -> bool:
    if not isinstance(attached, dict):
        return False
    origin = attached.get("origin")
    prompt = attached.get("prompt")
    return (
        attached.get("type") == "queued_command"
        and isinstance(origin, dict)
        and origin.get("kind") == "human"
        and isinstance(prompt, str)
        and bool(prompt.strip())
    )


def read_session(path: Path, since: datetime, *, owner: bool) -> SessionLog | None:  # noqa: C901, PLR0912, PLR0915
    tools: dict[str, tuple[str, str]] = {}
    seen: set[str] = set()
    usage = Counter[str]()
    reads = Counter[str]()
    outputs: list[tuple[int, str]] = []
    prompts: list[Correction] = []
    errors: list[str] = []
    interrupted = rejected = 0
    stamps: list[str] = []
    runs = TestRuns(path.stem[:8])
    pushes: set[str] = set()
    reviews = Counter[str]()
    task_stops = refused_pushes = peak = 0
    for entry in _entries(path, since):
        stamps.append(entry["timestamp"])
        message = entry.get("message") or {}
        content = message.get("content")
        if entry.get("type") == "assistant":
            reply = str(message.get("id") or entry.get("uuid"))
            if reply not in seen:
                seen.add(reply)
                u = message.get("usage") or {}
                usage["output"] += u.get("output_tokens", 0)
                usage["fresh"] += u.get("input_tokens", 0) + u.get(
                    "cache_creation_input_tokens",
                    0,
                )
                usage["cached"] += u.get("cache_read_input_tokens", 0)
                peak = max(
                    peak,
                    u.get("input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0)
                    + u.get("cache_read_input_tokens", 0),
                )
            for part in content if isinstance(content, list) else []:
                if part.get("type") == "tool_use":
                    target = _target(part.get("input") or {})
                    tools[part["id"]] = (part["name"], target)
                    if part["name"] == "Read":
                        reads[target] += 1
                    task_stops += part["name"] == "TaskStop"
                    command = str((part.get("input") or {}).get("command", ""))
                    preset = _test_preset(command) if part["name"] == "Bash" else None
                    if preset:
                        runs.launch(part["id"], preset, entry["timestamp"])
                        runs.run_without_output(part["id"], command)
                    if part["name"] == "Bash":
                        runs.read_summary_file(part["id"], command)
                    if part["name"] == "Bash" and GIT_PUSH.search(command):
                        pushes.add(part["id"])
                    if label := _review_label(part["name"], part.get("input") or {}):
                        reviews[label] += 1
            continue
        if entry.get("type") == "attachment":
            attached = entry.get("attachment")
            if isinstance(attached, dict) and isinstance(attached.get("prompt"), str):
                runs.notified(attached["prompt"], entry["timestamp"])
            if owner and _is_queued_owner_prompt(attached):
                prompts.append(
                    Correction(
                        at=entry["timestamp"][:16],
                        session=path.stem[:8],
                        text=str(attached["prompt"])[:TEXT_MAX],
                    ),
                )
            continue
        if entry.get("type") != "user":
            continue
        text = _text(content)
        if INTERRUPTED in text:
            interrupted += 1
        results = _tool_results(content)
        answers = [
            (p.get("tool_use_id", ""), _text(p.get("content"))) for p in results
        ] or ([(task, text)] if (task := _notified_task(text)) else [])
        for run_key, seen_text in answers:
            refused_pushes += run_key in pushes and REFUSED_PUSH in seen_text
            runs.answer(run_key, seen_text, entry["timestamp"])
        if owner and not results and _is_owner_prompt(entry, text):
            prompts.append(
                Correction(
                    at=entry["timestamp"][:16],
                    session=path.stem[:8],
                    text=text[:TEXT_MAX],
                ),
            )
        for part in results:
            result = _text(part.get("content"))
            name, target = tools.get(part.get("tool_use_id", ""), ("?", ""))
            outputs.append((len(result), f"{name} {target}"))
            if REJECTED in result:
                rejected += 1
            elif part.get("is_error"):
                errors.append(f"{name}: {_first_line(result)}")
    if not stamps:
        return None
    minutes = int((_when(stamps[-1]) - _when(stamps[0])).total_seconds() // 60)
    return SessionLog(
        cost=SessionCost(
            session=path.stem[:8],
            started=stamps[0][:16],
            minutes=minutes,
            subagents=0,
            output_tokens=usage["output"],
            fresh_input_tokens=usage["fresh"],
            cache_read_tokens=usage["cached"],
            peak_context_tokens=peak,
            rereads=[
                Repeat(what=w, count=c)
                for w, c in reads.most_common()
                if c >= REREAD_MIN
            ],
            biggest=[
                Repeat(what=w, count=n)
                for n, w in sorted(outputs, reverse=True)[:BIGGEST]
            ],
        ),
        prompts=prompts,
        interrupted=interrupted,
        rejected=rejected,
        errors=errors,
        test_runs=runs.all(),
        reviews=[Repeat(what=w, count=c) for w, c in reviews.most_common()],
        task_stops=task_stops,
        refused_pushes=refused_pushes,
    )


def _with_subagents(main: SessionLog, runs: list[SessionLog]) -> SessionLog:
    cost = main.cost.model_copy(
        update={
            "subagents": len(runs),
            "output_tokens": main.cost.output_tokens
            + sum(r.cost.output_tokens for r in runs),
            "fresh_input_tokens": main.cost.fresh_input_tokens
            + sum(r.cost.fresh_input_tokens for r in runs),
            "cache_read_tokens": main.cost.cache_read_tokens
            + sum(r.cost.cache_read_tokens for r in runs),
            "rereads": main.cost.rereads + [x for r in runs for x in r.cost.rereads],
        },
    )
    return main.model_copy(
        update={
            "cost": cost,
            "rejected": main.rejected + sum(r.rejected for r in runs),
            "errors": main.errors + [e for r in runs for e in r.errors],
            "test_runs": main.test_runs + [t for r in runs for t in r.test_runs],
            "reviews": _merged([*main.reviews, *(x for r in runs for x in r.reviews)]),
            "task_stops": main.task_stops + sum(r.task_stops for r in runs),
            "refused_pushes": main.refused_pushes + sum(r.refused_pushes for r in runs),
        },
    )


def sessions(logs: Path, since: datetime) -> list[SessionLog]:
    found = []
    cutoff = since.timestamp()
    for path in sorted(logs.glob("*.jsonl")):
        if path.stat().st_mtime < cutoff:
            continue
        main = read_session(path, since, owner=True)
        if main is None:
            continue
        runs = [
            run
            for sub in sorted((logs / path.stem / "subagents").glob("agent-*.jsonl"))
            if sub.stat().st_mtime >= cutoff
            and (run := read_session(sub, since, owner=False)) is not None
        ]
        found.append(_with_subagents(main, runs))
    return found


def _merged(repeats: Iterable[Repeat]) -> list[Repeat]:
    total = Counter[str]()
    for repeat in repeats:
        total[repeat.what] += repeat.count
    return [Repeat(what=w, count=c) for w, c in total.most_common()]


def guard_blocks(audit: Path, since: datetime) -> list[Repeat]:
    counts = Counter[str]()
    if not audit.is_file():
        return []
    for line in audit.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            print(f"self_learn: skipped a broken line in {audit}", file=sys.stderr)
            continue
        if _when(entry["at"]).replace(tzinfo=None) < since.astimezone().replace(
            tzinfo=None,
        ):
            continue
        for verdict in entry.get("verdicts", []):
            if verdict.endswith((":deny", ":block")):
                counts[f"{verdict} ({entry.get('event')} {entry.get('tool')})"] += 1
    return [Repeat(what=w, count=c) for w, c in counts.most_common(TOP)]


def digest(logs: Path, audit: Path, since: datetime) -> Digest:
    found = sessions(logs, since)
    errors = Counter(e for s in found for e in s.errors)
    return Digest(
        since=since.isoformat(timespec="minutes"),
        until=datetime.now(UTC).isoformat(timespec="minutes"),
        prompts=[p for s in found for p in s.prompts],
        interrupted=sum(s.interrupted for s in found),
        rejected=sum(s.rejected for s in found),
        failures=[Repeat(what=w, count=c) for w, c in errors.most_common(TOP)],
        blocks=guard_blocks(audit, since),
        costs=sorted((s.cost for s in found), key=lambda c: c.started),
        test_runs=[t for s in found for t in s.test_runs],
        reviews=_merged(r for s in found for r in s.reviews),
        task_stops=sum(s.task_stops for s in found),
        refused_pushes=sum(s.refused_pushes for s in found),
    )


def render(d: Digest) -> str:
    lines = [
        f"# Self-learn digest {d.since} -> {d.until}",
        "",
        (
            f"Sessions: {len(d.costs)}, subagent runs: "
            f"{sum(c.subagents for c in d.costs)}, "
            f"interrupted by the owner: {d.interrupted}, "
            f"tool calls the owner rejected: {d.rejected}"
        ),
        "",
        "## Owner prompts (corrections, repeats and frustration hide here)",
        *(f"- {p.at} {p.session}: {p.text}" for p in d.prompts),
        "",
        "## Failed tool calls",
        *(f"- {r.count}x {r.what}" for r in d.failures),
        "",
        "## Guard denies and blocks",
        *(f"- {r.count}x {r.what}" for r in d.blocks),
        "",
        "## test.sh runs (preset, duration, result)",
        *(f"- {t.session} {t.preset}: {t.seconds}s, {t.result}" for t in d.test_runs),
        "",
        "## Review rounds per feature (review agents and kimi review runs)",
        *(f"- {r.count}x {r.what}" for r in d.reviews),
        "",
        "## Stopped tasks and refused pushes",
        f"- TaskStop calls: {d.task_stops}",
        f"- refused pushes (failed to push): {d.refused_pushes}",
        "",
        "## Token cost per session",
        (
            "| session | started | min | subagents | output | fresh input | "
            "cache read | peak context | read 3x+ | biggest outputs (chars) |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {c.session} | {c.started} | {c.minutes} "
            f"| {c.subagents} | {c.output_tokens:,} "
            f"| {c.fresh_input_tokens:,} | {c.cache_read_tokens:,} "
            f"| {c.peak_context_tokens:,} "
            f"| {'; '.join(f'{r.count}x {r.what}' for r in c.rereads) or '-'} "
            f"| {'; '.join(f'{r.count:,} {r.what}' for r in c.biggest) or '-'} |"
            for c in d.costs
        ),
    ]
    return "\n".join(lines) + "\n"


def last_pass() -> datetime:
    if MARKER.is_file():
        return datetime.fromisoformat(MARKER.read_text(encoding="utf-8").strip())
    return datetime.now(UTC) - timedelta(days=DEFAULT_DAYS)


def main(argv: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if argv[:1] == ["--done"]:
        if SHIP_OPEN.exists() and argv[1:2] != ["--allow-empty"]:
            d = digest(project_logs(), AUDIT, last_pass())
            if not d.costs:
                print(
                    "self_learn: no sessions found during an open ship; "
                    "review the evidence or pass --allow-empty to acknowledge it",
                )
                return 1
        MARKER.write_text(
            datetime.now(UTC).isoformat(timespec="seconds"),
            encoding="utf-8",
        )
        print(f"self_learn: marked done at {MARKER.read_text(encoding='utf-8')}")
        return 0
    since = (
        datetime.now(UTC) - timedelta(days=float(argv[1]))
        if argv[:1] == ["--days"]
        else last_pass()
    )
    d = digest(project_logs(), AUDIT, since)
    report = OUT / f"{datetime.now(UTC):%Y-%m-%d-%H%M%S}.md"
    report.write_text(render(d), encoding="utf-8", newline="\n")
    print(
        f"self_learn: {report} ({len(d.costs)} "
        f"sessions, {len(d.prompts)} owner prompts, "
        f"{d.interrupted} interrupts, {d.rejected} rejected calls, "
        f"{sum(r.count for r in d.failures)} failed calls, "
        f"{sum(r.count for r in d.blocks)} guard blocks)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
