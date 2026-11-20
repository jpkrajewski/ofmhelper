import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "self-learn"
MARKER = OUT / "last.txt"
AUDIT = ROOT / "reports" / "hooks" / "audit.jsonl"
DEFAULT_DAYS = 7
METADATA_LINE = '{"type":"'
KEPT_TYPES = ('{"type":"user"', '{"type":"assistant"')
INTERRUPTED = "[Request interrupted by user"
REJECTED = "The user doesn't want to proceed with this tool use"
CONTINUED = "This session is being continued from a previous conversation"
REREAD_MIN = 3
TOP = 15
BIGGEST = 3
TEXT_MAX = 400
WHAT_MAX = 140


class Correction(BaseModel):
    at: str
    session: str
    text: str


class Repeat(BaseModel):
    what: str
    count: int


class SessionCost(BaseModel):
    session: str
    started: str
    minutes: int
    subagents: int
    output_tokens: int
    fresh_input_tokens: int
    cache_read_tokens: int
    rereads: list[Repeat]
    biggest: list[Repeat]


class SessionLog(BaseModel):
    cost: SessionCost
    prompts: list[Correction]
    interrupted: int
    rejected: int
    errors: list[str]


class Digest(BaseModel):
    since: str
    until: str
    prompts: list[Correction]
    interrupted: int
    rejected: int
    failures: list[Repeat]
    blocks: list[Repeat]
    costs: list[SessionCost]


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


def _is_owner_prompt(entry: dict[str, Any], text: str) -> bool:
    origin = entry.get("origin") or {}
    return (
        origin.get("kind") == "human"
        and not entry.get("isMeta")
        and bool(text.strip())
        and not text.startswith(CONTINUED)
    )


def read_session(path: Path, since: datetime, *, owner: bool) -> SessionLog | None:
    tools: dict[str, tuple[str, str]] = {}
    seen: set[str] = set()
    usage = Counter[str]()
    reads = Counter[str]()
    outputs: list[tuple[int, str]] = []
    prompts: list[Correction] = []
    errors: list[str] = []
    interrupted = rejected = 0
    stamps: list[str] = []
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
                    "cache_creation_input_tokens", 0
                )
                usage["cached"] += u.get("cache_read_input_tokens", 0)
            for part in content if isinstance(content, list) else []:
                if part.get("type") == "tool_use":
                    target = _target(part.get("input") or {})
                    tools[part["id"]] = (part["name"], target)
                    if part["name"] == "Read":
                        reads[target] += 1
            continue
        if entry.get("type") != "user":
            continue
        text = _text(content)
        if INTERRUPTED in text:
            interrupted += 1
        results = _tool_results(content)
        if owner and not results and _is_owner_prompt(entry, text):
            prompts.append(
                Correction(
                    at=entry["timestamp"][:16],
                    session=path.stem[:8],
                    text=text[:TEXT_MAX],
                )
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
        }
    )
    return main.model_copy(
        update={
            "cost": cost,
            "rejected": main.rejected + sum(r.rejected for r in runs),
            "errors": main.errors + [e for r in runs for e in r.errors],
        }
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
            tzinfo=None
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
    )


def render(d: Digest) -> str:
    lines = [
        f"# Self-learn digest {d.since} -> {d.until}",
        "",
        (
            f"Sessions: {len(d.costs)}, subagent runs: {sum(c.subagents for c in d.costs)}, "
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
        "## Token cost per session",
        "| session | started | min | subagents | output | fresh input | cache read | read 3x+ | biggest outputs (chars) |",
        "|---|---|---|---|---|---|---|---|---|",
        *(
            f"| {c.session} | {c.started} | {c.minutes} | {c.subagents} | {c.output_tokens:,} "
            f"| {c.fresh_input_tokens:,} | {c.cache_read_tokens:,} "
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
        MARKER.write_text(
            datetime.now(UTC).isoformat(timespec="seconds"), encoding="utf-8"
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
        f"self_learn: {report} ({len(d.costs)} sessions, {len(d.prompts)} owner prompts, "
        f"{d.interrupted} interrupts, {d.rejected} rejected calls, "
        f"{sum(r.count for r in d.failures)} failed calls, {sum(r.count for r in d.blocks)} guard blocks)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
