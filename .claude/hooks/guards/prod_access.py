import re
from pathlib import Path

from guards.event import ROOT, Guard, HookEvent, Verdict, deny, owner_said_ship
from guards.secrets import production_hosts
from guards.shell import Statement, shell_statements

NAME = "prod_access"
REMOTE = {"ssh", "scp", "rsync", "sftp"}
SHELLS = {"bash", "sh"}
SANCTIONED = {"scripts/prod_to_dev.sh"}
PROD_MARKS = ("deploy.local.env", "OFM_DEPLOY_HOST")
LOCAL_SWITCH = re.compile(r"\b[A-Z][A-Z0-9_]*_LOCAL=(1|true)\b")
LIVE = (
    "CLAUDE.md PROTECT PAID AND LIVE SYSTEMS + Verify on dev: this reaches the production server. "
    "Prove it on the dev stack instead (a script that can reach production gets a <NAME>_LOCAL=1 "
    "switch that runs it against dev); running it against production is the owner's step "
    "(their word ship/deploy). Prod data for dev comes only through scripts/prod_to_dev.sh."
)


def script_of(statement: Statement) -> str:
    words = statement.words[1:] if statement.program in SHELLS else statement.words
    return (
        words[0].replace("\\", "/").removeprefix("./")
        if words and words[0].endswith(".sh")
        else ""
    )


def reaches_production(statement: Statement, cwd: Path) -> bool:
    if statement.program in REMOTE:
        hosts = [*production_hosts(), "$OFM_DEPLOY_HOST", "${OFM_DEPLOY_HOST}"]
        return any(host in word for word in statement.words for host in hosts)
    script = script_of(statement)
    if not script or script in SANCTIONED:
        return False
    path = cwd / script
    return path.is_file() and any(
        mark in path.read_text(encoding="utf-8") for mark in PROD_MARKS
    )


def check(event: HookEvent) -> Verdict | None:
    if owner_said_ship(event) or LOCAL_SWITCH.search(event.command):
        return None
    cwd = Path(event.cwd or ROOT)
    hits = (s for s in shell_statements(event) if reaches_production(s, cwd))
    return deny(NAME, LIVE) if next(hits, None) else None


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse"}),
    tools=frozenset({"Bash", "PowerShell"}),
    check=check,
)
