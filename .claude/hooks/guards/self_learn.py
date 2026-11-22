import re
from datetime import datetime

from guards.event import ROOT, Guard, HookEvent, Markers, Verdict, block
from guards.shell import Statement, shell_statements

NAME = "self_learn_after_deploy"
DONE = ROOT / "reports" / "self-learn" / "last.txt"
SHELLS = frozenset({"Bash", "PowerShell"})
DEPLOY_WORKFLOW = re.compile(r"deploy", re.IGNORECASE)
LEARN = (
    "CLAUDE.md SELF-LEARNING: a commit was made, a PR was merged or a deploy was started "
    "in this session (owner 2026-11-22: self reflect after every approved commit or "
    "deploy). Once the commit or the production deploy is done and reported, run the "
    "self-learn skill: it reads every saved session log since the last pass and turns "
    "owner corrections, failures and token waste into checks and BRAIN.md lines, then "
    "scripts/self_learn.py --done. If it was only a dependency PR into dev, or the "
    "commit or the deploy failed, say so and stop again."
)


def git_subcommand(words: list[str]) -> str:
    rest = iter(words[1:])
    for word in rest:
        if word in ("-C", "-c"):
            next(rest, None)
        elif not word.startswith("-"):
            return word
    return ""


def deploys(statement: Statement) -> bool:
    words = statement.words
    if statement.program == "git":
        return git_subcommand(words) == "commit"
    if statement.program != "gh":
        return False
    if words[1:3] == ["pr", "merge"]:
        return True
    return words[1:3] == ["workflow", "run"] and any(
        DEPLOY_WORKFLOW.search(word) for word in words[3:]
    )


def learned_at() -> float:
    if not DONE.is_file():
        return 0.0
    return datetime.fromisoformat(DONE.read_text(encoding="utf-8").strip()).timestamp()


def check(event: HookEvent) -> Verdict | None:
    markers = Markers.of(event.model_copy(update={"agent_id": ""}))
    if event.hook_event_name == "PostToolUse":
        if any(deploys(statement) for statement in shell_statements(event)):
            markers.set("deployed")
        return None
    deployed = markers.since("deployed")
    if (
        event.stop_hook_active
        or not deployed
        or learned_at() > deployed
        or markers.since("learn-asked") > deployed
    ):
        return None
    markers.set("learn-asked")
    return block(NAME, LEARN)


GUARD = Guard(
    name=NAME, events=frozenset({"PostToolUse", "Stop"}), tools=SHELLS, check=check
)
