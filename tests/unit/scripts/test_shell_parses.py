"""Every shell script and workflow `run:` block parses, and the deploy paths'
remote scripts stay one single-quoted string.

Both deploy paths ship the server's script as `remote_script='...'`. One
apostrophe in a comment in there ("this build's own", 2026-11-08, caught
before it shipped) ends the string: the rest runs on the runner, or the step
dies on a quote it cannot close.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml
from repo_paths import REPO

SCRIPTS = sorted((REPO / "scripts").glob("*.sh"))
WORKFLOWS = sorted((REPO / ".github/workflows").glob("*.yml"))
DEPLOYS = [REPO / "scripts/deploy.sh", REPO / ".github/workflows/deploy.yml"]

# `${{ ... }}` is GitHub's, filled in before bash ever sees the text.
_EXPRESSION = re.compile(r"\$\{\{.*?\}\}")
# The body of `remote_script='` up to the line that is only the closing quote.
_REMOTE = re.compile(
    r"remote_script='\n(.*?)\n[ \t]*'[ \t]*$", re.DOTALL | re.MULTILINE
)


def _bash_n(text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-n"], input=text, capture_output=True, text=True, check=False
    )


def _runs(path: Path) -> list[tuple[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        (f"{job_name}/{step.get('name', step.get('uses', '?'))}", step["run"])
        for job_name, job in data["jobs"].items()
        for step in job.get("steps", [])
        if "run" in step
    ]


def test_there_is_something_to_check():
    assert {p.name for p in SCRIPTS} >= {"deploy.sh", "test.sh"}


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_every_script_parses(path: Path):
    result = _bash_n(path.read_text(encoding="utf-8"))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_run_block_parses(path: Path):
    for where, run in _runs(path):
        result = _bash_n(_EXPRESSION.sub("X", run))
        assert result.returncode == 0, f"{path.name} {where}: {result.stderr}"


def _remote_script_problems(text: str) -> list[str]:
    bodies = _REMOTE.findall(text)
    if not bodies:
        return ["no remote_script='...' found"]
    problems = []
    for body in bodies:
        if "'" in body:
            problems.append("an apostrophe ends remote_script")
        if (result := _bash_n(body)).returncode != 0:
            problems.append(result.stderr)
    return problems


@pytest.mark.parametrize("path", DEPLOYS, ids=lambda p: p.name)
def test_the_remote_deploy_script_is_one_quoted_string(path: Path):
    assert _remote_script_problems(path.read_text(encoding="utf-8")) == []


def test_the_apostrophe_of_2026_11_08_is_caught():
    script = (
        "remote_script='\n"
        "  # Only layers used in the last day are kept (this build's own, so\n"
        "  docker builder prune -af --filter until=24h >/dev/null\n"
        "'\n"
    )
    assert _remote_script_problems(script) == ["an apostrophe ends remote_script"]
