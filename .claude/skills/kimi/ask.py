"""Hands one task to Kimi Code CLI (the owner's second agent) and prints only
its final answer.

    python .claude/skills/kimi/ask.py review   <task.md> [name]
    python .claude/skills/kimi/ask.py research <task.md> [name]
    python .claude/skills/kimi/ask.py ask      <task.md> [name]

`review` and `ask` are read-only; `research` may use Kimi's WebSearch and
FetchURL. The mode's rules go in front of the task file. Kimi's whole
stream (JSON lines) is kept in $TEMP/kimi-<name>.jsonl, its answer in
$TEMP/kimi-<name>.md. Warns when the checkout changed during the run (Kimi
shares this working tree with every other agent).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_S = int(os.environ.get("KIMI_TIMEOUT", "1800"))
_NO_WRITES = (
    "Do not edit, create or delete any file in this repository. No git command "
    "that changes state (add, commit, stash, checkout, reset, push, merge). No "
    "docker, no tests, no ssh."
)
RULES = {
    "review": (
        f"READ-ONLY CODE REVIEW. {_NO_WRITES} Only read files and run git "
        "diff/status/show/log. Report real defects only: bugs, wrong logic, "
        "security holes, a check that can never fail, data loss, a broken "
        "deploy. No style nits, no praise, no feature ideas. Answer format, "
        "nothing else: one line per finding `path:line | HIGH/MEDIUM/LOW | "
        "defect | evidence (quote the code)`, or `NO FINDINGS`."
    ),
    "research": (
        f"RESEARCH. {_NO_WRITES} Use WebSearch and FetchURL. Prefer official "
        "docs, release notes, changelogs, source code and issue trackers over "
        "blogs; note each source's date (today is {today}). Answer format: the "
        "answer first, short and direct; then `Evidence:` one line per fact "
        "with its URL and date; then `Unsure:` what you could not verify. "
        "Never state a guess as a fact."
    ),
    "ask": f"READ-ONLY. {_NO_WRITES} Answer short and direct; say what you checked.",
}


def _kimi() -> str:
    exe = Path.home() / ".kimi-code" / "bin" / "kimi.exe"
    return str(exe) if exe.is_file() else (shutil.which("kimi") or "kimi")


def _status() -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout


def _answer(stream: str) -> tuple[str, str]:
    """(last assistant message, resume command) from Kimi's JSON lines."""
    answer, resume = "", ""
    for line in stream.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        content = event.get("content")
        if (
            event.get("role") == "assistant"
            and isinstance(content, str)
            and content.strip()
        ):
            answer = content
        if event.get("type") == "session.resume_hint":
            resume = event.get("command", "")
    return answer, resume


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in RULES:  # noqa: PLR2004
        print(__doc__)
        return 2
    mode, task = sys.argv[1], Path(sys.argv[2])
    name = sys.argv[3] if len(sys.argv) > 3 else f"{mode}-{datetime.now(UTC):%H%M%S}"  # noqa: PLR2004
    rules = RULES[mode].replace("{today}", f"{datetime.now(UTC):%Y-%m-%d}")
    prompt = f"{rules}\n\n{task.read_text(encoding='utf-8')}"
    out_dir = Path(tempfile.gettempdir())
    stream_file, answer_file = (
        out_dir / f"kimi-{name}.jsonl",
        out_dir / f"kimi-{name}.md",
    )

    before = _status()
    try:
        run = subprocess.run(
            [_kimi(), "-p", prompt, "--output-format", "stream-json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_S,
            check=False,
        )
        stream, code = run.stdout + run.stderr, run.returncode
    except subprocess.TimeoutExpired as exc:
        stream = (
            (exc.stdout or b"").decode("utf-8", "replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        code = 124
    stream_file.write_text(stream, encoding="utf-8")
    answer, resume = _answer(stream)
    answer_file.write_text(answer + "\n", encoding="utf-8")

    print(answer or f"(no answer; exit {code}; see {stream_file})")
    if resume:
        print(f"\nfollow-up with the same context: {resume}")
    if (after := _status()) != before:
        print("\nkimi-ask: WARNING: the checkout changed during the Kimi run")
        print("before:\n" + before + "after:\n" + after)
    print(f"kimi-ask: exit {code}; stream {stream_file}; answer {answer_file}")
    return code


if __name__ == "__main__":
    sys.exit(main())
