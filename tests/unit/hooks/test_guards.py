import base64
import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[3]
DISPATCHER = REPO / ".claude" / "hooks" / "guard.py"
DEV_COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.dev.yml"
REVIEWER = {
    "description": "Review the diff",
    "subagent_type": "caveman:cavecrew-reviewer",
}
KIMI = {"command": "python .claude/skills/kimi/ask.py review task.md x"}
UI_RUN = f"{DEV_COMPOSE} --profile test run --rm tests uv run --frozen python scripts/ui_check.py /spoof"
FRESH_PASS = "ui_check: PASS reports/ui-check/spoof-new/report.md"
OLD_PASS = "ui_check: PASS reports/ui-check/spoof-old/report.md"
FAILED = "ui_check: FAIL reports/ui-check/spoof-failed/report.md"
FAILED_AS_PASS = "ui_check: PASS reports/ui-check/spoof-failed/report.md"


class Answer(BaseModel):
    decision: str
    reason: str
    context: str
    stderr: str


class Hooks:
    def __init__(self, root: Path, session: str = "s1", agent: str = "") -> None:
        self.root = root
        self.session = session
        self.agent = agent

    def send(self, event: str, **fields: object) -> Answer:
        payload = {
            "session_id": self.session,
            "hook_event_name": event,
            "cwd": str(self.root),
            **fields,
        }
        if self.agent:
            payload["agent_id"] = self.agent
        done = subprocess.run(
            [sys.executable, str(DISPATCHER)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "CLAUDE_GUARDS_ROOT": str(self.root),
                "PYDANTIC_DISABLE_PLUGINS": "__all__",
            },
            check=True,
        )
        out = json.loads(done.stdout) if done.stdout.strip() else {}
        specific = out.get("hookSpecificOutput", {})
        return Answer(
            decision=specific.get("permissionDecision")
            or out.get("decision")
            or "allow",
            reason=specific.get("permissionDecisionReason") or out.get("reason") or "",
            context=specific.get("additionalContext") or "",
            stderr=done.stderr,
        )

    def bash(self, command: str, background: bool = False) -> Answer:
        tool_input = {"command": command, "run_in_background": background}
        return self.send("PreToolUse", tool_name="Bash", tool_input=tool_input)

    def prompt(self, text: str) -> Answer:
        return self.send("UserPromptSubmit", prompt=text)

    def edit(
        self, relative: str, old: str = "a", new: str = "b", when: str = "PreToolUse"
    ) -> Answer:
        tool_input = {
            "file_path": str(self.root / relative),
            "old_string": old,
            "new_string": new,
        }
        return self.send(when, tool_name="Edit", tool_input=tool_input)

    def skill(self, name: str) -> Answer:
        return self.send("PostToolUse", tool_name="Skill", tool_input={"skill": name})


FAKE_DOCKER = (
    "import pathlib, sys, time\n"
    "mode = pathlib.Path({mode!r}).read_text()\n"
    "args = sys.argv[1:]\n"
    "if args[:1] == ['info']:\n"
    "    sys.exit(1 if mode == 'down' else 0)\n"
    "if mode == 'hang':\n"
    "    time.sleep(30)\n"
    "if mode == 'fail':\n"
    "    sys.stderr.write('no such container')\n"
    "    sys.exit(1)\n"
    "if mode == 'garbage':\n"
    "    print('not a number')\n"
    "    sys.exit(0)\n"
    "busy = mode.partition(':')[2].split(',')\n"
    "for line in sys.stdin:\n"
    "    print(1 if line.split()[1].removeprefix('rq:wip:') in busy else 0)\n"
)


@pytest.fixture
def docker_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    mode = tmp_path / "docker_mode"
    mode.write_text("up")
    fake = tmp_path / "fake_docker.py"
    fake.write_text(FAKE_DOCKER.format(mode=str(mode)))
    monkeypatch.setenv("CLAUDE_GUARDS_DOCKER", f"{sys.executable} {fake}")
    return mode


@pytest.fixture
def hooks(tmp_path: Path, docker_mode: Path) -> Hooks:
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "check_no_comments.py", tmp_path / "scripts")
    shutil.copy(REPO / "scripts" / "import_graph.py", tmp_path / "scripts")
    shutil.copy(REPO / "CLAUDE.md", tmp_path / "CLAUDE.md")
    (tmp_path / "src" / "ofmhelpers" / "features" / "old").mkdir(parents=True)
    (tmp_path / "src" / "ofmhelpers" / "features" / "old" / "service.py").write_text(
        "x = 1\n"
    )
    (tmp_path / "deploy.local.env").write_text("OFM_DEPLOY_HOST=ubuntu@203.0.113.7\n")
    for script in ("pull_backup.sh", "prod_to_dev.sh"):
        (tmp_path / "scripts" / script).write_text(
            'source deploy.local.env\nssh "$OFM_DEPLOY_HOST" true\n'
        )
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return Hooks(tmp_path)


@pytest.mark.parametrize(
    ("command", "guard_text"),
    [
        ("git commit -m x", "never commit"),
        ("git -C . push origin dev", "never commit"),
        ("git reset --hard HEAD~1", "never commit"),
        ("gh pr merge 101 --merge", "never commit"),
        ('powershell -Command "git commit -m x"', "never commit"),
        ("PowerShell -c git push origin dev", "never commit"),
        ("cmd /c git reset --hard HEAD~1", "never commit"),
        ('iex "git commit -m x"', "never commit"),
        ("nice gh pr merge 101 --merge", "never commit"),
        ("git stash", "Never git stash"),
        ("git stash pop", "Never git stash"),
        ("mypy src", "runs in Docker"),
        ("uv run --frozen ruff check src", "runs in Docker"),
        ("node --check src/x.js", "runs in Docker"),
        ("pytest tests/unit", "ONLY via scripts/test.sh"),
        (
            f"{DEV_COMPOSE} run --rm tests uv run --frozen pytest tests",
            "ONLY via scripts/test.sh",
        ),
        ("scripts/test.sh", "only the tests of what you touched"),
        ("scripts/test.sh push", "only the tests of what you touched"),
        ("scripts/test.sh e2e", "only the tests of what you touched"),
        ("bash scripts/test.sh live", "only the tests of what you touched"),
        ("scripts/test.sh paid", "Paid tests"),
        ("scripts/test.sh tests/unit/test_x.py | tail -20", "never pipe"),
        ("scripts/test.sh tests/unit/test_x.py 2>&1 | tee out.log", "never pipe"),
        ("ruff check --select RUF100 --fix", "deletes every # noqa"),
        ("find / -name worker_pool.py", "whole disk"),
        ("docker compose up -d", "takes both files"),
        (f"{DEV_COMPOSE} down -v", "deletes the data volumes"),
        ('ssh prod "bash -s" < deploy.sh', "bash -s"),
        ("scp .env ubuntu@host:/home/ubuntu/app/", "Never copy a .env"),
        ("cd src && git commit -am wip", "never commit"),
        (
            "OFM_BACKUP_DIR=/tmp/x timeout 500 bash scripts/pull_backup.sh 2>&1 | tail -15",
            "production",
        ),
        ('ssh -i key ubuntu@203.0.113.7 "cat app/.env"', "production"),
        ("rsync -a $OFM_DEPLOY_HOST:app/uploads ./uploads", "production"),
        ('bash -c "git push origin dev"', "never commit"),
        ('eval "git commit -m x"', "never commit"),
        ("echo $(git commit -m x)", "never commit"),
        ("env GIT_TRACE=1 git commit -m x", "never commit"),
        ("uvx mypy src", "runs in Docker"),
        ("env -i PATH=/usr/bin pytest tests", "ONLY via scripts/test.sh"),
        ("uvx --from mypy==1.0 mypy src", "runs in Docker"),
        ("uvx --python 3.13 mypy src", "runs in Docker"),
        ("uv tool run mypy src", "runs in Docker"),
        ("git.exe commit -m x", "never commit"),
        ("C:/tools/Git/bin/git.exe commit -m x", "never commit"),
        ("/usr/bin/git push origin dev", "never commit"),
        ("nice -n 10 git commit -m x", "never commit"),
        ("setsid git commit -m x", "never commit"),
        ("stdbuf -o L git commit -m x", "never commit"),
        ("ionice -c 3 git commit -m x", "never commit"),
        ("xargs -a files.txt -n 1 git commit -m x", "never commit"),
    ],
)
def test_a_command_breaking_a_rule_is_denied_with_its_rule(
    hooks: Hooks, command: str, guard_text: str
) -> None:
    answer = hooks.bash(command)

    assert answer.decision == "deny"
    assert guard_text in answer.reason


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        "git log --oneline -5",
        "git diff --stat",
        "git merge --ff-only origin/main",
        f"{DEV_COMPOSE} run --rm tests uv run --frozen mypy",
        f"{DEV_COMPOSE} up -d postgres redis",
        "docker ps | grep postgres",
        "pre-commit run --files src/x.py",
        "grep -n 'a|b|git commit' CLAUDE.md",
        "scripts/test.sh e2e tests/e2e/test_threads_page.py",
        "find src -name '*.py'",
        "uv run --frozen --no-sync python .claude/hooks/ruff_on_edit.py",
        f"{DEV_COMPOSE} run --rm --no-deps -T tests uv run --frozen shellcheck scripts/test.sh",
        "git diff scripts/test.sh",
        "bash scripts/prod_to_dev.sh --reuse",
        "OFM_BACKUP_LOCAL=1 OFM_BACKUP_DIR=/tmp/bk bash scripts/pull_backup.sh",
        "grep -rn pytest tests/",
        "rg -n 'import pytest' docs/",
        "python - <<'EOF'\nimport pytest\np = 'scripts/pull_backup.sh'\nEOF",
        "cmd='git commit -m x && bash scripts/pull_backup.sh'; echo \"$cmd\"",
        "git -C /tmp/elsewhere commit -m scratch",
        "git stash list",
        "git stash show -p stash@{0}",
    ],
)
def test_an_ordinary_command_passes(hooks: Hooks, command: str) -> None:
    assert hooks.bash(command).decision == "allow"


def encoded(text: str) -> str:
    return base64.b64encode(text.encode("utf-16-le")).decode()


def nested_in_bash(command: str, depth: int) -> str:
    for _ in range(depth):
        command = "bash -c " + shlex.quote(command)
    return command


@pytest.mark.parametrize(
    "command",
    [
        f"powershell -EncodedCommand {encoded('git commit -m x')}",
        f"pwsh -enc {encoded('git commit -m x')}",
        f"powershell -NoProfile -ec {encoded('git push origin dev')}",
        f"powershell -e {encoded('git reset --hard HEAD~1')}",
        f"powershell -EncodedCommand {encoded('powershell -Command git commit')}",
        "powershell -EncodedCommand not-base64!",
        "powershell -EncodedCommand",
        "bash <<'EOF'\ngit commit -m x\nEOF",
        "cat <<EOF | bash\ngit commit -m x\nEOF",
        "sh - <<EOF\ngit push origin dev\nEOF",
        "$'git' commit -m x",
        'g"it" commit -m x',
        "$'g\\x69t' commit -m x",
        nested_in_bash("git commit -m x", 5),
    ],
)
def test_encoded_heredoc_quoted_and_deeply_nested_commands_are_not_a_way_around(
    hooks: Hooks, command: str
) -> None:
    assert hooks.bash(command).decision == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git \\\n  push origin dev",
        "git \\\r\n  push origin dev",
        'echo "$(printf \'"\')"; git push --force',
        "echo 'unterminated; git push origin dev",
    ],
)
def test_a_line_continuation_or_an_unparseable_command_is_not_a_way_around(
    hooks: Hooks, command: str
) -> None:
    assert hooks.bash(command).decision == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git `\n  push origin dev",
        "g`it push origin dev",
        "git `\r\n  commit -m x",
    ],
)
def test_powershell_continuations_and_escapes_still_name_the_program(
    hooks: Hooks, command: str
) -> None:
    answer = hooks.send(
        "PreToolUse", tool_name="PowerShell", tool_input={"command": command}
    )

    assert answer.decision == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git status",
        f"{DEV_COMPOSE} ps",
        "cat README.md | head",
        "grep -n x CLAUDE.md",
        "bash scripts/test.sh tests/unit",
        "git log --oneline \\\n  -5",
    ],
)
def test_ordinary_commands_are_not_refused_by_the_shell_parser(
    hooks: Hooks, command: str
) -> None:
    assert hooks.bash(command).decision == "allow"


@pytest.mark.parametrize(
    "command", ['g"it" commit -m x', "g'it' push origin dev", "&'git' commit -m x"]
)
def test_quote_tricks_in_powershell_still_name_the_program(
    hooks: Hooks, command: str
) -> None:
    answer = hooks.send(
        "PreToolUse", tool_name="PowerShell", tool_input={"command": command}
    )

    assert answer.decision == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "cat <<'EOF'\ngit commit -m x\nEOF",
        f"powershell -EncodedCommand {encoded('Get-Date')}",
        nested_in_bash("git status", 2),
        "echo $(echo $(echo hi)) $(echo $(echo hi))",
    ],
)
def test_harmless_encoded_heredoc_and_nested_commands_pass(
    hooks: Hooks, command: str
) -> None:
    assert hooks.bash(command).decision == "allow"


def test_a_scoped_test_run_passes_and_asks_for_the_report(hooks: Hooks) -> None:
    answer = hooks.bash(
        "scripts/test.sh tests/unit/test_x.py tests/integration/test_y.py"
    )

    assert answer.decision == "allow"
    assert "10 slowest" in answer.context


def test_the_owners_ship_word_opens_git_and_the_full_suite_for_that_prompt_only(
    hooks: Hooks,
) -> None:
    hooks.prompt("ok ship it")

    assert hooks.bash("git commit -m feat").decision == "allow"
    assert hooks.bash("scripts/test.sh deploy").decision == "allow"
    assert hooks.bash("git push origin dev", background=True).decision == "allow"
    assert "run_in_background" in hooks.bash("git push origin dev").reason
    piped = hooks.bash("git push origin dev 2>&1 | tail -20", background=True)
    assert piped.decision == "deny"
    assert "push log to a file" in piped.reason

    hooks.prompt("<task-notification><status>completed</status></task-notification>")
    assert hooks.bash("git commit -m fix").decision == "allow"

    hooks.prompt("now fix the button")
    assert hooks.bash("git commit -m fix").decision == "deny"


def test_the_push_log_path_is_the_one_the_ship_skill_names(hooks: Hooks) -> None:
    hooks.prompt("push")
    piped = hooks.bash("git push origin dev | tail -5", background=True)

    named = re.search(r"> (\S+push\.log)", piped.reason)

    assert named is not None
    assert named[1] == "reports/loops/push.log"
    ship = (REPO / ".claude" / "skills" / "ship" / "SKILL.md").read_text("utf-8")
    assert named[1] in ship


@pytest.mark.parametrize(
    ("prompt", "opens"),
    [
        ("gate", True),
        ("  Gate!\n", True),
        ("GATE.", True),
        ("rerun the gate", False),
        ("the gate is red, fix the test", False),
        ("gate it", False),
    ],
)
def test_only_the_owners_bare_word_gate_opens_a_gate_rerun(
    hooks: Hooks, prompt: str, opens: bool
) -> None:
    hooks.prompt(prompt)

    marker = hooks.root / "reports" / "hooks" / "state" / "s1-main" / "owner-gate"
    assert marker.is_file() is opens


def test_a_subagent_never_inherits_the_owners_word(hooks: Hooks) -> None:
    hooks.prompt("ship")
    subagent = Hooks(hooks.root, session=hooks.session, agent="agent-7")

    assert subagent.bash("git commit -m x").decision == "deny"


ONE_OPEN = '0 {"open": 1, "more": false}'
THREE_OPEN = '0 {"open": 3, "more": false}'
NONE_OPEN = '0 {"open": 0, "more": false}'
MORE_PAGES = '0 {"open": 0, "more": true}'


def _guard_hook_budget_s(event: str = "PreToolUse") -> float:
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())
    return next(
        hook["timeout"]
        for entry in settings["hooks"][event]
        for hook in entry["hooks"]
        if "guard.py" in hook["command"]
    )


FAKE_GH = (
    "import json, pathlib, sys, time\n"
    "pathlib.Path({asked!r}).write_text(chr(10).join(sys.argv[1:]))\n"
    "text = pathlib.Path({answer!r}).read_text()\n"
    "time.sleep(10 if text == 'hang' else 0)\n"
    "code, out = text.split(' ', 1)\n"
    "if '--jq' in sys.argv and out.startswith('{{\"data\"'):\n"
    "    if sys.argv[sys.argv.index('--jq') + 1] != {count!r}:\n"
    "        sys.stderr.write('the guard changed its jq expression: update this fake')\n"
    "        sys.exit(3)\n"
    "    pr = json.loads(out)['data']['repository']['pullRequest']\n"
    "    threads, reviews = pr['reviewThreads'], pr['latestReviews']\n"
    "    unresolved = [n for n in threads['nodes'] if not n['isResolved']]\n"
    "    asked = [n for n in reviews['nodes'] if n['state'] == 'CHANGES_REQUESTED']\n"
    "    more = threads['pageInfo']['hasNextPage'] or reviews['pageInfo']['hasNextPage']\n"
    "    out = json.dumps({{'open': len(unresolved) + len(asked), 'more': more}})\n"
    "print(out)\n"
    "sys.exit(int(code))\n"
)


def guard_count() -> str:
    sys.path.insert(0, str(REPO / ".claude" / "hooks"))
    try:
        return importlib.import_module("guards.pr_comments").COUNT
    finally:
        sys.path.remove(str(REPO / ".claude" / "hooks"))


def graphql_answer(
    threads: list[bool],
    reviews: list[str],
    more_threads: bool = False,
    more_reviews: bool = False,
) -> str:
    pull_request = {
        "reviewThreads": {
            "nodes": [{"isResolved": resolved} for resolved in threads],
            "pageInfo": {"hasNextPage": more_threads},
        },
        "latestReviews": {
            "nodes": [{"state": state} for state in reviews],
            "pageInfo": {"hasNextPage": more_reviews},
        },
    }
    return "0 " + json.dumps({"data": {"repository": {"pullRequest": pull_request}}})


@pytest.fixture
def github(hooks: Hooks, monkeypatch: pytest.MonkeyPatch) -> Path:
    answer = hooks.root / "gh_answer"
    asked = hooks.root / "gh_asked"
    fake = hooks.root / "fake_gh.py"
    fake.write_text(
        FAKE_GH.format(asked=str(asked), answer=str(answer), count=guard_count())
    )
    monkeypatch.setenv("CLAUDE_GUARDS_GH", f"{sys.executable} {fake}")
    hooks.prompt("ship")
    return answer


@pytest.mark.parametrize(
    ("answer", "command", "reason"),
    [
        (ONE_OPEN, "gh pr merge 103 --merge", "YOU CANT MERGE IF THERE ARE COMMENTS"),
        (THREE_OPEN, "gh pr merge 7 --merge", "PR #7 has 3"),
        ("1 HTTP 502", "gh pr merge 103 --merge", "Could not read"),
        (MORE_PAGES, "gh pr merge 103 --merge", "more than 100"),
    ],
)
def test_a_pr_with_open_review_comments_is_never_merged_even_on_the_ship_word(
    hooks: Hooks, github: Path, answer: str, command: str, reason: str
) -> None:
    github.write_text(answer)

    verdict = hooks.bash(command)

    assert verdict.decision == "deny"
    assert reason in verdict.reason


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        (graphql_answer([True, False, False], ["APPROVED"]), "PR #103 has 2"),
        (graphql_answer([True], ["CHANGES_REQUESTED"]), "PR #103 has 1"),
        (graphql_answer([False], ["CHANGES_REQUESTED"]), "PR #103 has 2"),
        (graphql_answer([True], [], more_threads=True), "more than 100"),
        (graphql_answer([], [], more_reviews=True), "more than 100"),
    ],
)
def test_the_guards_own_graphql_query_and_count_decide_the_merge(
    hooks: Hooks, github: Path, answer: str, reason: str
) -> None:
    github.write_text(answer)

    verdict = hooks.bash("gh pr merge 103 --merge")

    assert verdict.decision == "deny"
    assert reason in verdict.reason
    asked = (hooks.root / "gh_asked").read_text()
    assert "reviewThreads" in asked
    assert "latestReviews" in asked
    assert "isResolved" in asked


def test_resolved_threads_and_plain_reviews_let_the_graphql_answer_merge(
    hooks: Hooks, github: Path
) -> None:
    github.write_text(graphql_answer([True, True], ["APPROVED", "COMMENTED"]))

    assert hooks.bash("gh pr merge 103 --merge").decision == "allow"


def test_a_pr_without_open_review_comments_merges_on_the_ship_word(
    hooks: Hooks, github: Path
) -> None:
    github.write_text(NONE_OPEN)

    assert hooks.bash("gh pr merge 103 --merge").decision == "allow"
    asked = (hooks.root / "gh_asked").read_text().splitlines()
    assert {"owner={owner}", "repo={repo}", "number=103"} <= set(asked)
    assert hooks.bash("rtk gh pr merge 103 --merge").decision == "allow"
    assert hooks.bash("gh pr view 103 --comments").decision == "allow"


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 103",
        "gh pr merge --merge",
        "gh pr merge my-branch --merge",
        "gh pr merge 103 --merge -d",
        "gh pr merge 103 --squash",
        "gh.exe pr merge 103 --merge",
        "/c/tools/gh.exe pr merge 103 --merge",
        "gh pr merge 7 --merge -R other/repo",
        "gh -R other/repo pr merge 7 --merge",
        "gh pr merge https://github.com/other/repo/pull/7 --merge",
        "GH_REPO=other/repo gh pr merge 7 --merge",
        "echo y | gh pr merge 103 --merge",
        "ls & gh pr merge 103 --merge",
        "cd .. && gh pr merge 103 --merge",
        "gh pr merge 1 --merge && gh pr merge 2 --merge",
        "bash -c 'gh pr merge 103 --merge'",
        "gh api -X PUT repos/o/r/pulls/7/merge",
        "gh api --method PUT /repos/o/r/pulls/7/merge/",
        "GH.exe api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
        "cd ../other && gh pr merge 7 --merge",
        "cd ../other; gh pr merge 7 --merge",
        "pushd ../other && gh pr merge 7 --merge",
        'powershell -Command "gh pr merge 103 --merge"',
        "pwsh -NoProfile -c gh pr merge 103 --merge",
        "powershell.exe -command gh pr merge 103 --merge",
        "cmd /c gh pr merge 103 --merge",
        'cmd.exe /C "gh pr merge 103 --merge"',
        'iex "gh pr merge 103 --merge"',
        "Invoke-Expression 'gh pr merge 103 --merge'",
        "eval gh pr merge 103 --merge",
        "bash -lc 'gh pr merge 103 --merge'",
        "echo y | bash -c 'gh pr merge 103 --merge'",
        "echo $(gh pr merge 103 --merge)",
        "echo 7 | xargs gh pr merge 7 --merge",
        "nice -n 5 gh pr merge 7 --merge",
        "'C:/tools/GitHub CLI/gh.exe' pr merge 7 --merge",
        "gh pr -R other/repo merge 7 --merge",
    ],
)
def test_only_the_canonical_merge_spelling_is_let_through(
    hooks: Hooks, github: Path, command: str
) -> None:
    github.write_text(NONE_OPEN)

    verdict = hooks.bash(command)

    assert verdict.decision == "deny"
    assert "canonical form" in verdict.reason


@pytest.mark.parametrize(
    "command",
    [
        "gh pr list --search merge",
        "gh pr view 7 --json mergeable,mergedAt",
        "gh pr list --state merged --search 'merge queue'",
        "gh run list --workflow merge",
    ],
)
def test_only_the_pr_merge_subcommand_pair_counts_as_a_merge(
    hooks: Hooks, github: Path, command: str
) -> None:
    github.write_text(ONE_OPEN)

    assert hooks.bash(command).decision == "allow"


def test_a_merge_run_outside_the_repo_root_is_refused(
    hooks: Hooks, github: Path
) -> None:
    github.write_text(NONE_OPEN)
    (hooks.root / "sub").mkdir()
    merge = {"command": "gh pr merge 103 --merge"}

    verdict = hooks.send(
        "PreToolUse", tool_name="Bash", tool_input=merge, cwd=str(hooks.root / "sub")
    )

    assert verdict.decision == "deny"
    assert "canonical form" in verdict.reason


def test_a_github_that_does_not_answer_stops_the_merge_within_the_hook_budget(
    hooks: Hooks, github: Path
) -> None:
    github.write_text("hang")
    started = time.monotonic()

    verdict = hooks.bash("gh pr merge 103 --merge")

    assert verdict.decision == "deny"
    assert "Could not read" in verdict.reason
    assert time.monotonic() - started < _guard_hook_budget_s() - 4


def test_an_open_ship_keeps_git_open_into_the_next_session(hooks: Hooks) -> None:
    (hooks.root / "reports" / "hooks").mkdir(parents=True)
    (hooks.root / "reports" / "hooks" / "ship.open").write_text("ed20257")

    assert (
        Hooks(hooks.root, session="next")
        .bash("git push origin dev", background=True)
        .decision
        == "allow"
    )


def test_paid_runs_need_the_word_paid_in_the_owners_prompt(hooks: Hooks) -> None:
    hooks.prompt("run the paid test for apify")

    assert (
        hooks.bash(
            "scripts/test.sh paid tests/contract/test_apify_contract.py"
        ).decision
        == "allow"
    )


def test_a_running_test_suite_blocks_a_second_run_and_edits_under_src(
    hooks: Hooks,
) -> None:
    (hooks.root / "reports").mkdir()
    lock = hooks.root / "reports" / ".running"
    lock.write_text("123 run default")

    assert "already going" in hooks.bash("scripts/test.sh tests/unit/test_x.py").reason
    assert "is running" in hooks.edit("src/ofmhelpers/x.py").reason
    assert hooks.edit("README.md").decision == "allow"

    two_hours_ago = time.time() - 7200
    os.utime(lock, (two_hours_ago, two_hours_ago))
    assert hooks.bash("scripts/test.sh tests/unit/test_x.py").decision == "allow"


def test_the_architecture_baseline_only_shrinks(hooks: Hooks) -> None:
    baseline = "tests/unit/architecture/baseline.json"
    (hooks.root / "tests" / "unit" / "architecture").mkdir(parents=True)
    (hooks.root / baseline).write_text('[\n"a",\n"b",\n"c"\n]\n')

    assert hooks.edit(baseline, old='"a",', new='"a",\n"z",').decision == "deny"
    assert hooks.edit(baseline, old='"a",\n"b",', new='"a",').decision == "allow"
    assert (
        hooks.edit(baseline, old='"a",\n"b",\n"c"', new='"a-b-c"').decision == "allow"
    )


def test_an_edit_waits_for_the_skills_its_area_needs(hooks: Hooks) -> None:
    router = "src/ofmhelpers/web/routers/downloads/media.py"
    (hooks.root / router).parent.mkdir(parents=True)
    (hooks.root / router).write_text("a = 1\n")
    researched(hooks)
    write_note(hooks)
    new_route = "def download():\n    pass"

    denied = hooks.edit(router, new=new_route)
    assert denied.decision == "deny"
    assert "senior-python-rules, fastapi, rest-api, failure-modes" in denied.reason

    for name in (
        "anthropic-skills:senior-python-rules",
        "fastapi",
        "rest-api",
        "failure-modes",
    ):
        hooks.skill(name)
    assert hooks.edit(router, new=new_route).decision == "allow"


def test_architecture_is_needed_for_a_new_feature_file_not_an_existing_one(
    hooks: Hooks,
) -> None:
    hooks.skill("senior-python-rules")
    hooks.skill("failure-modes")

    assert "architecture" in hooks.edit("src/ofmhelpers/features/new/service.py").reason
    assert hooks.edit("src/ofmhelpers/features/old/service.py").decision == "allow"


def test_failure_modes_is_needed_for_new_code_in_a_route_page_job_or_integration(
    hooks: Hooks,
) -> None:
    script = hooks.root / "src" / "ofmhelpers" / "web" / "static" / "js" / "spoof.js"
    script.parent.mkdir(parents=True)
    script.write_text("function load() {}\n")
    hooks.skill("senior-python-rules")
    researched(hooks)
    write_note(hooks)
    service = "src/ofmhelpers/features/old/service.py"
    spoof = "src/ofmhelpers/web/static/js/spoof.js"
    new_code = ((service, "def run():\n    pass"), (spoof, "function save() {}"))

    assert hooks.edit(service, old="x = 1", new="x = 2").decision == "allow"
    assert hooks.edit(spoof, old="load() {}", new="load() { return 1; }").decision == (
        "allow"
    )
    for path, new in new_code:
        denied = hooks.edit(path, new=new)
        assert denied.decision == "deny"
        assert "failure-modes" in denied.reason
    assert "failure-modes" in hooks.edit("src/ofmhelpers/features/new/job.py").reason

    hooks.skill("failure-modes")
    for path, new in new_code:
        assert hooks.edit(path, new=new).decision == "allow"
    assert hooks.edit("README.md").decision == "allow"


def test_a_changed_signature_is_not_new_code_but_a_new_name_is(hooks: Hooks) -> None:
    hooks.prompt("fix the bug")
    hooks.skill("senior-python-rules")
    service = "src/ofmhelpers/features/old/service.py"
    (hooks.root / service).write_text("def run(a):\n    return a\n")

    for new in ("async def run(a: int) -> int:", "def run(a, b=1):"):
        assert hooks.edit(service, old="def run(a):", new=new).decision == "allow"
    js = hooks.root / "src" / "ofmhelpers" / "web" / "static" / "js" / "load.js"
    js.parent.mkdir(parents=True)
    js.write_text("function load() {}\n")
    assert (
        hooks.edit(
            "src/ofmhelpers/web/static/js/load.js",
            old="function load() {}",
            new="export async function load(page) {}",
        ).decision
        == "allow"
    )

    added = hooks.edit(
        service, old="def run(a):", new="def helper():\n    pass\n\n\ndef run(a):"
    )
    assert added.decision == "deny"
    assert "adds new code" in added.reason
    assert "failure-modes" in added.reason


def test_a_subagent_uses_the_main_sessions_skills_but_not_another_agents(
    hooks: Hooks,
) -> None:
    Hooks(hooks.root, session=hooks.session, agent="agent-2").skill(
        "senior-python-rules"
    )
    third = Hooks(hooks.root, session=hooks.session, agent="agent-3")

    assert third.edit("tests/unit/test_x.py").decision == "deny"

    hooks.skill("senior-python-rules")
    assert third.edit("tests/unit/test_x.py").decision == "allow"


def test_a_comment_written_into_source_is_sent_back(hooks: Hooks) -> None:
    source = hooks.root / "src" / "ofmhelpers" / "commented.py"
    source.write_text("x = 1  # explains x\n")

    answer = hooks.edit("src/ofmhelpers/commented.py", when="PostToolUse")

    assert answer.decision == "block"
    assert "src/ofmhelpers/commented.py:1" in answer.reason


def test_the_owners_deploy_word_lets_a_production_step_through(hooks: Hooks) -> None:
    hooks.prompt("deploy it")

    assert hooks.bash("bash scripts/pull_backup.sh").decision == "allow"


def test_a_source_file_written_from_the_shell_gets_the_edit_checks(
    hooks: Hooks,
) -> None:
    (hooks.root / "src" / "ofmhelpers" / "shell_made.py").write_bytes(
        b"x = 2  # why\r\n"
    )
    sed = {"command": "sed -i s/1/2/ src/ofmhelpers/shell_made.py"}

    answer = hooks.send(
        "PostToolUse", tool_name="Bash", tool_input=sed, duration_ms=300
    )

    assert answer.decision == "block"
    assert "CRLF written into src/ofmhelpers/shell_made.py" in answer.reason
    assert "src/ofmhelpers/shell_made.py:1" in answer.reason
    assert "senior-python-rules" in answer.reason


def shell_wrote(hooks: Hooks, command: str) -> Answer:
    return hooks.send(
        "PostToolUse",
        tool_name="Bash",
        tool_input={"command": command},
        duration_ms=300,
    )


def test_an_existing_python_file_rewritten_from_the_shell_is_sent_back(
    hooks: Hooks,
) -> None:
    hooks.skill("senior-python-rules")
    service = "src/ofmhelpers/features/old/service.py"
    subprocess.run(["git", "-C", str(hooks.root), "add", service], check=True)

    (hooks.root / service).write_text("x = 2\n")
    rewrite = f"python - <<'EOF'\nfrom pathlib import Path\nPath('{service}').write_text('x = 2')\nEOF"
    answer = shell_wrote(hooks, rewrite)
    assert answer.decision == "block"
    assert f"{service}: an existing .py was rewritten from the shell" in answer.reason
    assert "Change an existing .py with Edit" in answer.reason

    (hooks.root / service).write_text("x = 3\n")
    assert (
        "rewritten from the shell"
        not in shell_wrote(hooks, f"sed -i s/2/3/ {service}").reason
    )
    (hooks.root / "src" / "ofmhelpers" / "fresh.py").write_text("y = 1\n")
    assert (
        "rewritten from the shell"
        not in shell_wrote(
            hooks, "cat > src/ofmhelpers/fresh.py <<'EOF'\ny = 1\nEOF"
        ).reason
    )


def committed(hooks: Hooks, path: str) -> None:
    git = ["git", "-C", str(hooks.root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", path], check=True)
    subprocess.run(
        [*git, "-c", "commit.gpgsign=false", "commit", "-qm", "x"], check=True
    )


def test_a_name_added_from_the_shell_to_existing_code_needs_failure_modes(
    hooks: Hooks,
) -> None:
    hooks.skill("senior-python-rules")
    service = "src/ofmhelpers/features/old/service.py"
    committed(hooks, service)
    append = f"python -c \"open('{service}', 'a').write('def run(): pass')\""

    (hooks.root / service).write_text("x = 1\ny = 2\n")
    nothing_new = shell_wrote(hooks, append)
    assert "rewritten from the shell" in nothing_new.reason
    assert "failure-modes" not in nothing_new.reason

    (hooks.root / service).write_text("x = 1\ndef run(): pass\n")
    added = shell_wrote(hooks, append)
    assert added.decision == "block"
    assert "failure-modes" in added.reason

    hooks.skill("failure-modes")
    (hooks.root / service).write_text("x = 1\ndef run(): pass\n")
    loaded = shell_wrote(hooks, append)
    assert "rewritten from the shell" in loaded.reason
    assert "failure-modes" not in loaded.reason


def test_new_code_written_from_the_shell_is_sent_back_at_stop_until_researched(
    hooks: Hooks,
) -> None:
    hooks.prompt("build the guard")
    hooks.skill("senior-python-rules")
    (hooks.root / "scripts" / "made_in_shell.py").write_text(
        "def run():\n    return 1\n"
    )
    shell_wrote(
        hooks, "cat > scripts/made_in_shell.py <<'EOF'\ndef run():\n    return 1\nEOF"
    )

    stop = hooks.send("Stop")
    assert stop.decision == "block"
    assert "written from the shell (scripts/made_in_shell.py)" in stop.reason
    assert "codegraph_explore for the pattern" in stop.reason
    assert hooks.send("Stop").decision == "allow"

    (hooks.root / "tests" / "unit").mkdir(parents=True)
    (hooks.root / "tests" / "unit" / "test_made.py").write_text("x = 1\n")
    shell_wrote(hooks, "cat > tests/unit/test_made.py <<'EOF'\nx = 1\nEOF")
    assert hooks.send("Stop").decision == "allow"

    researched(hooks)
    write_note(hooks)
    (hooks.root / "scripts" / "second.py").write_text("def go():\n    return 2\n")
    shell_wrote(hooks, "cat > scripts/second.py <<'EOF'\ndef go():\n    return 2\nEOF")
    assert hooks.send("Stop").decision == "allow"


def test_a_read_only_shell_call_skips_the_write_checks(hooks: Hooks) -> None:
    (hooks.root / "src" / "ofmhelpers" / "shell_made.py").write_bytes(
        b"x = 2  # why\r\n"
    )

    answer = hooks.send(
        "PostToolUse", tool_name="Bash", tool_input={"command": "git status"}
    )

    assert answer.decision == "allow"


def test_the_production_host_never_lands_in_a_tracked_file(hooks: Hooks) -> None:
    write = {
        "file_path": str(hooks.root / "README.md"),
        "content": "ssh ubuntu@203.0.113.7",
    }

    answer = hooks.send("PreToolUse", tool_name="Write", tool_input=write)

    assert answer.decision == "deny"
    assert "public" in answer.reason


def test_stopping_after_source_edits_asks_once_for_an_independent_review(
    hooks: Hooks,
) -> None:
    hooks.edit("src/ofmhelpers/web/x.py", when="PostToolUse")

    assert hooks.send("Stop").decision == "block"
    assert hooks.send("Stop").decision == "allow"

    hooks.edit("src/ofmhelpers/web/x.py", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Bash", tool_input=KIMI)
    stop = hooks.send("SubagentStop")
    assert stop.decision == "block"
    assert "caveman:cavecrew-reviewer" in stop.reason

    hooks.edit("src/ofmhelpers/web/x.py", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    assert hooks.send("SubagentStop").decision == "allow"


def test_only_the_cavecrew_reviewer_counts_as_the_review(hooks: Hooks) -> None:
    hooks.edit("src/ofmhelpers/web/x.py", when="PostToolUse")
    look_alike = {"description": "Review the diff", "subagent_type": "general-purpose"}
    hooks.send("PostToolUse", tool_name="Agent", tool_input=look_alike)

    stop = hooks.send("Stop")
    assert stop.decision == "block"
    assert "caveman:cavecrew-reviewer" in stop.reason

    hooks.edit("src/ofmhelpers/web/x.py", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    assert hooks.send("Stop").decision == "allow"


def test_source_edited_from_the_shell_also_needs_a_review(hooks: Hooks) -> None:
    hooks.skill("senior-python-rules")
    (hooks.root / "src" / "ofmhelpers" / "made_by_python.py").write_text("x = 1\n")
    write = {
        "command": "python - <<'EOF'\nopen('src/ofmhelpers/made_by_python.py','w')\nEOF"
    }
    hooks.send("PostToolUse", tool_name="Bash", tool_input=write, duration_ms=300)

    stop = hooks.send("SubagentStop")

    assert stop.decision == "block"
    assert "caveman:cavecrew-reviewer" in stop.reason


def test_worker_code_changed_without_a_restart_is_sent_back_once(hooks: Hooks) -> None:
    first = hooks.edit("src/ofmhelpers/features/repurpose/tasks.py", when="PostToolUse")
    assert "restart worker media-worker" in first.context
    again = hooks.edit("src/ofmhelpers/features/repurpose/tasks.py", when="PostToolUse")
    assert again.context == ""
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)

    stop = hooks.send("SubagentStop")
    assert stop.decision == "block"
    assert "restart worker media-worker" in stop.reason

    hooks.edit("src/ofmhelpers/features/repurpose/tasks.py", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    restart = {
        "command": f"{DEV_COMPOSE} restart worker media-worker preview-worker edit-worker sweeps"
    }
    hooks.send("PostToolUse", tool_name="Bash", tool_input=restart)
    assert hooks.send("SubagentStop").decision == "allow"


def test_a_doc_beside_worker_code_needs_no_worker_restart(hooks: Hooks) -> None:
    hooks.edit("src/ofmhelpers/media/repurpose/CLAUDE.md", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)

    assert hooks.send("SubagentStop").decision == "allow"


def worker_code_changed(hooks: Hooks) -> Answer:
    return hooks.edit("src/ofmhelpers/features/repurpose/tasks.py", when="PostToolUse")


def restart_command(text: str) -> str:
    return text.partition("restart ")[2].partition(",")[0]


def test_a_queue_with_a_running_job_is_not_asked_to_restart(
    hooks: Hooks, docker_mode: Path
) -> None:
    docker_mode.write_text("busy:media,default")

    note = worker_code_changed(hooks)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    stop = hooks.send("SubagentStop")

    for text in (note.context, stop.reason):
        assert restart_command(text) == "preview-worker edit-worker sweeps"
        assert "Do NOT restart worker media-worker" in text
    assert stop.decision == "block"


@pytest.mark.parametrize("mode", ["fail", "hang", "garbage"])
def test_a_queue_that_cannot_be_read_is_never_asked_to_restart_a_worker(
    hooks: Hooks, docker_mode: Path, mode: str
) -> None:
    docker_mode.write_text(mode)

    worker_code_changed(hooks)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    stop = hooks.send("SubagentStop")

    assert restart_command(stop.reason) == "sweeps"
    assert (
        "Do NOT restart worker media-worker preview-worker edit-worker" in stop.reason
    )


def test_a_restart_of_the_idle_services_ends_the_ask_while_a_queue_is_busy(
    hooks: Hooks, docker_mode: Path
) -> None:
    docker_mode.write_text("busy:media")
    worker_code_changed(hooks)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    restart = {
        "command": f"{DEV_COMPOSE} restart worker preview-worker edit-worker sweeps"
    }
    hooks.send("PostToolUse", tool_name="Bash", tool_input=restart)

    assert hooks.send("SubagentStop").decision == "allow"


def restart_of(
    hooks: Hooks, command: str, event: str = "PostToolUse", **fields: object
) -> None:
    hooks.send(event, tool_name="Bash", tool_input={"command": command}, **fields)


def test_a_restart_of_one_service_leaves_the_others_asked_for(hooks: Hooks) -> None:
    worker_code_changed(hooks)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    restart_of(hooks, f"{DEV_COMPOSE} restart worker")

    stop = hooks.send("SubagentStop")

    assert stop.decision == "block"
    assert (
        restart_command(stop.reason) == "media-worker preview-worker edit-worker sweeps"
    )


@pytest.mark.parametrize(
    "command",
    [
        'echo "restart worker media-worker preview-worker edit-worker sweeps"',
        "docker ps | grep restart worker",
        f"{DEV_COMPOSE} logs worker",
    ],
)
def test_a_command_that_only_mentions_a_restart_is_no_restart(
    hooks: Hooks, command: str
) -> None:
    worker_code_changed(hooks)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    restart_of(hooks, command)

    assert hooks.send("SubagentStop").decision == "block"


@pytest.mark.parametrize(
    ("event", "fields"),
    [
        ("PostToolUseFailure", {}),
        ("PostToolUse", {"tool_response": {"exit_code": 1}}),
    ],
)
def test_a_failed_restart_does_not_count(
    hooks: Hooks, event: str, fields: dict[str, object]
) -> None:
    worker_code_changed(hooks)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    restart_of(
        hooks,
        f"{DEV_COMPOSE} restart worker media-worker preview-worker edit-worker sweeps",
        event,
        **fields,
    )

    assert hooks.send("SubagentStop").decision == "block"


def test_an_up_without_service_names_restarts_every_service(hooks: Hooks) -> None:
    worker_code_changed(hooks)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    restart_of(hooks, f"{DEV_COMPOSE} up -d --build")

    assert hooks.send("SubagentStop").decision == "allow"


def test_a_subagent_worker_edit_is_asked_of_the_main_session_not_the_subagent(
    hooks: Hooks,
) -> None:
    subagent = Hooks(hooks.root, agent="agent-7")
    subagent.edit("src/ofmhelpers/features/repurpose/tasks.py", when="PostToolUse")
    subagent.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)

    assert subagent.send("SubagentStop").decision == "allow"

    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    stop = hooks.send("Stop")
    assert stop.decision == "block"
    assert "restart worker media-worker" in stop.reason

    restart_of(
        hooks,
        f"{DEV_COMPOSE} restart worker media-worker preview-worker edit-worker sweeps",
    )
    assert hooks.send("Stop").decision == "allow"


def test_one_call_that_restarts_and_edits_worker_code_still_asks_for_a_restart(
    hooks: Hooks,
) -> None:
    hooks.skill("senior-python-rules")
    (hooks.root / "src" / "ofmhelpers" / "made_by_python.py").write_text("x = 1\n")
    both = (
        f"{DEV_COMPOSE} restart worker media-worker preview-worker edit-worker sweeps"
        " && python -c \"open('src/ofmhelpers/made_by_python.py','w')\""
    )
    hooks.send(
        "PostToolUse", tool_name="Bash", tool_input={"command": both}, duration_ms=300
    )
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)

    stop = hooks.send("Stop")

    assert stop.decision == "block"
    assert "restart worker" in stop.reason


def test_a_git_that_fails_after_a_shell_write_is_a_note_not_a_crash(
    hooks: Hooks,
) -> None:
    (hooks.root / ".git" / "HEAD").write_text("garbage")
    write = {"command": "python -c \"open('src/ofmhelpers/made_by_python.py','w')\""}

    answer = hooks.send(
        "PostToolUse", tool_name="Bash", tool_input=write, duration_ms=300
    )

    assert "could not be checked for worker code" in answer.context
    assert "guard restart_workers" not in answer.stderr


def test_the_restart_advice_says_to_recheck_that_no_job_runs(hooks: Hooks) -> None:
    assert "re-check that no job runs" in worker_code_changed(hooks).context


def test_a_hanging_redis_stays_inside_the_post_call_budget(
    hooks: Hooks, docker_mode: Path
) -> None:
    docker_mode.write_text("hang")
    started = time.monotonic()

    worker_code_changed(hooks)

    assert time.monotonic() - started < _guard_hook_budget_s("PostToolUse") - 3


WINDOWS_HOST = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the fake Docker Desktop is a .sh script; these tests run in the Linux tests container",
)


def start_desktop(hooks: Hooks, monkeypatch: pytest.MonkeyPatch) -> Path:
    started = hooks.root / "desktop_started"
    launcher = hooks.root / "fake_desktop.sh"
    launcher.write_text(f"#!/bin/sh\ntouch {started}\n")
    launcher.chmod(0o755)
    monkeypatch.setenv("CLAUDE_GUARDS_DOCKER_DESKTOP", str(launcher))
    return started


def wait_for(path: Path) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


@WINDOWS_HOST
def test_docker_desktop_is_started_at_session_start_when_the_engine_is_down(
    hooks: Hooks, docker_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = start_desktop(hooks, monkeypatch)
    docker_mode.write_text("down")

    answer = hooks.send("SessionStart")

    assert "Docker Desktop was down at session start" in answer.context
    assert wait_for(started)


@WINDOWS_HOST
def test_docker_desktop_is_left_alone_when_the_engine_answers(
    hooks: Hooks, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = start_desktop(hooks, monkeypatch)

    answer = hooks.send("SessionStart")

    assert answer.context == ""
    assert not wait_for(started)


@WINDOWS_HOST
def test_a_missing_docker_cli_counts_as_a_down_engine(
    hooks: Hooks, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = start_desktop(hooks, monkeypatch)
    monkeypatch.setenv("CLAUDE_GUARDS_DOCKER", str(hooks.root / "no_such_docker"))

    answer = hooks.send("SessionStart")

    assert "Docker Desktop was down" in answer.context
    assert wait_for(started)


@WINDOWS_HOST
def test_a_docker_desktop_that_cannot_launch_is_reported_not_crashed(
    hooks: Hooks, docker_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = hooks.root / "not_executable"
    broken.write_text("x")
    broken.chmod(0o644)
    monkeypatch.setenv("CLAUDE_GUARDS_DOCKER_DESKTOP", str(broken))
    docker_mode.write_text("down")

    answer = hooks.send("SessionStart")

    assert "could not be started" in answer.context


def test_nothing_is_started_when_docker_desktop_is_not_installed(
    hooks: Hooks, docker_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_GUARDS_DOCKER_DESKTOP", str(hooks.root / "absent.exe"))
    docker_mode.write_text("down")

    assert hooks.send("SessionStart").context == ""


@pytest.fixture
def hanging_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text("#!/bin/sh\nexec sleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def test_a_git_that_hangs_does_not_hold_a_secrets_check_past_its_timeout(
    hooks: Hooks, hanging_git: None
) -> None:
    started = time.monotonic()

    answer = hooks.send(
        "PreToolUse",
        tool_name="Write",
        tool_input={
            "file_path": str(hooks.root / "notes.txt"),
            "content": "host 203.0.113.7",
        },
    )

    assert answer.decision == "deny"
    assert "a file git tracks" in answer.reason
    assert time.monotonic() - started < 10


def test_a_hanging_git_costs_all_post_call_guards_one_shared_budget(
    hooks: Hooks, hanging_git: None
) -> None:
    write = {
        "command": "python - <<'EOF'\nopen('src/ofmhelpers/features/old/service.py','w')\nEOF"
    }
    started = time.monotonic()

    answer = hooks.send(
        "PostToolUse", tool_name="Bash", tool_input=write, duration_ms=300
    )

    assert answer.decision == "allow"
    assert time.monotonic() - started < _guard_hook_budget_s("PostToolUse") - 3


def test_a_git_that_hangs_leaves_a_visible_note_not_a_silent_pass(
    hooks: Hooks, hanging_git: None
) -> None:
    write = {
        "command": "python - <<'EOF'\nopen('src/ofmhelpers/shell_made.py','w')\nEOF"
    }

    answer = hooks.send(
        "PostToolUse", tool_name="Bash", tool_input=write, duration_ms=300
    )

    assert answer.decision == "allow"
    assert "could not be checked" in answer.context


@pytest.mark.parametrize(
    "command",
    [
        "cp /tmp/made.py src/ofmhelpers/shell_made.py",
        "mv /tmp/made.py src/ofmhelpers/shell_made.py",
        "git apply made.patch",
        "patch -p1 < made.patch",
        "ruff format src",
        "ruff check --fix src",
        "prettier --write src/ofmhelpers/shell_made.py",
    ],
)
def test_copy_move_patch_and_formatters_get_the_shell_write_checks(
    hooks: Hooks, command: str
) -> None:
    (hooks.root / "src" / "ofmhelpers" / "shell_made.py").write_bytes(b"x = 2\r\n")

    answer = shell_wrote(hooks, command)

    assert answer.decision == "block"
    assert "CRLF written into src/ofmhelpers/shell_made.py" in answer.reason


def test_a_name_appended_from_the_shell_to_an_existing_script_needs_research(
    hooks: Hooks,
) -> None:
    hooks.prompt("build the guard")
    hooks.skill("senior-python-rules")
    tool = hooks.root / "scripts" / "old_tool.py"
    tool.write_text("x = 1\n")
    committed(hooks, "scripts/old_tool.py")
    append = "python -c \"open('scripts/old_tool.py', 'a').write('y')\""

    tool.write_text("x = 1\ny = 2\n")
    shell_wrote(hooks, append)
    assert "written from the shell" not in hooks.send("Stop").reason

    tool.write_text("x = 1\ndef run():\n    return 1\n")
    shell_wrote(hooks, append)
    stop = hooks.send("Stop")
    assert stop.decision == "block"
    assert "written from the shell (scripts/old_tool.py)" in stop.reason


def ui_check_ran(hooks: Hooks, last_line: str, command: str = UI_RUN) -> None:
    response = {
        "stdout": f"ui_check: page load\nui_check: [pass] page load: ok\n{last_line}\n",
        "stderr": "",
        "interrupted": False,
        "isImage": False,
    }
    hooks.send(
        "PostToolUse",
        tool_name="Bash",
        tool_input={"command": command},
        tool_response=response,
        duration_ms=60_000,
    )


def ui_report(hooks: Hooks, folder: str, result: str = "PASS", age_s: int = 0) -> str:
    report = hooks.root / "reports" / "ui-check" / folder / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        f"# UI check /spoof\n\n- Result: {result} (0 of 7 steps failed)\n"
    )
    if age_s:
        written = time.time() - age_s
        os.utime(report, (written, written))
    return f"ui_check: {result} reports/ui-check/{folder}/report.md"


def test_a_web_only_change_needs_no_worker_restart(hooks: Hooks) -> None:
    hooks.edit("src/ofmhelpers/web/templates/x.html", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    ui_check_ran(hooks, ui_report(hooks, "spoof-new"))

    assert hooks.send("SubagentStop").decision == "allow"


def test_a_ui_change_is_looked_at_before_it_is_done(hooks: Hooks) -> None:
    hooks.edit("src/ofmhelpers/web/static/css/pages.css", when="PostToolUse")

    stop = hooks.send("Stop")
    assert stop.decision == "block"
    assert "ui_screens" in stop.reason

    hooks.edit("src/ofmhelpers/web/static/css/pages.css", when="PostToolUse")
    shot = {
        "command": f"{DEV_COMPOSE} --profile test run --rm tests uv run --frozen python scripts/ui_screens.py r60"
    }
    hooks.send("PostToolUse", tool_name="Bash", tool_input=shot)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    assert hooks.send("Stop").decision == "allow"


def test_a_js_change_counts_as_looked_at_only_after_the_ui_check(hooks: Hooks) -> None:
    script = "src/ofmhelpers/web/static/js/spoof.js"
    first = hooks.edit(script, when="PostToolUse")
    assert "ui-check skill" in first.context
    assert hooks.edit(script, when="PostToolUse").context == ""
    shot = {"command": "uv run python scripts/ui_screens.py r60"}
    hooks.send("PostToolUse", tool_name="Bash", tool_input=shot)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)

    stop = hooks.send("Stop")
    assert stop.decision == "block"
    assert "scripts/ui_check.py" in stop.reason

    hooks.edit(script, when="PostToolUse")
    hooks.send(
        "PostToolUse",
        tool_name="Bash",
        tool_input={"command": "cat scripts/ui_check.py"},
    )
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    assert hooks.send("Stop").decision == "block"

    hooks.edit(script, when="PostToolUse")
    ui_check_ran(hooks, ui_report(hooks, "spoof-new"))
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    assert hooks.send("Stop").decision == "allow"


@pytest.mark.parametrize(
    ("last_lines", "command"),
    [
        (FAILED, UI_RUN),
        (
            "ui_check: DOWN the dev app at http://ofmhelpers:8000 does not answer",
            UI_RUN,
        ),
        ("", UI_RUN),
        ("ui_check: PASS", UI_RUN),
        (f"{FRESH_PASS}\n{FAILED}", UI_RUN),
        (FRESH_PASS, "cat scripts/ui_check.py"),
        (FRESH_PASS, "echo python ui_check passed"),
        (FRESH_PASS, f"python -c \"print('{FRESH_PASS}')\""),
        (FRESH_PASS, f"uv run python -c \"print('{FRESH_PASS}')\""),
        (f"{FAILED}\n{OLD_PASS}", f'{UI_RUN}; echo "{OLD_PASS}"'),
        (f"{FAILED}\n{FAILED_AS_PASS}", f'{UI_RUN}; echo "{FAILED_AS_PASS}"'),
        ("ui_check: PASS reports/ui-check/../../outside/report.md", UI_RUN),
    ],
)
def test_a_ui_check_counts_only_with_a_fresh_passing_report_of_a_real_run(
    hooks: Hooks, last_lines: str, command: str
) -> None:
    ui_report(hooks, "spoof-new")
    ui_report(hooks, "spoof-old", age_s=3600)
    ui_report(hooks, "spoof-failed", result="FAIL")
    (hooks.root / "outside").mkdir()
    (hooks.root / "outside" / "report.md").write_text("- Result: PASS (0 of 7)\n")
    script = "src/ofmhelpers/web/static/js/spoof.js"
    hooks.edit(script, when="PostToolUse")
    ui_check_ran(hooks, last_lines, command)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)

    stop = hooks.send("Stop")

    assert stop.decision == "block"
    assert "scripts/ui_check.py" in stop.reason


@pytest.mark.parametrize(
    "reported",
    [
        "reports/ui-check/spoof-new/report.md",
        "/app/reports/ui-check/spoof-new/report.md",
    ],
)
def test_a_real_ui_check_pass_counts_with_its_report(
    hooks: Hooks, reported: str
) -> None:
    ui_report(hooks, "spoof-new")
    script = "src/ofmhelpers/web/static/js/spoof.js"
    hooks.edit(script, when="PostToolUse")
    ui_check_ran(hooks, f"ui_check: PASS {reported}")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)

    assert hooks.send("Stop").decision == "allow"


def test_a_screenshot_counts_only_when_ui_screens_runs(hooks: Hooks) -> None:
    css = "src/ofmhelpers/web/static/css/pages.css"
    hooks.edit(css, when="PostToolUse")
    mention = {"command": "grep -n shoot scripts/ui_screens.py"}
    hooks.send("PostToolUse", tool_name="Bash", tool_input=mention)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    assert "ui_screens" in hooks.send("Stop").reason

    hooks.edit(css, when="PostToolUse")
    one_page = {
        "command": 'uv run python -c "from scripts.ui_screens import shoot, sign_in"'
    }
    hooks.send("PostToolUse", tool_name="Bash", tool_input=one_page)
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    assert hooks.send("Stop").decision == "allow"


def test_saying_kimi_is_not_a_review(hooks: Hooks) -> None:
    hooks.edit("src/ofmhelpers/x.py", when="PostToolUse")
    hooks.send(
        "PostToolUse", tool_name="Bash", tool_input={"command": "echo kimi done"}
    )

    assert hooks.send("SubagentStop").decision == "block"


def test_a_shell_write_is_not_blamed_for_a_file_it_does_not_name(hooks: Hooks) -> None:
    (hooks.root / "src" / "ofmhelpers" / "other_session.py").write_bytes(
        b"x = 1  # c\r\n"
    )
    sed = {"command": "sed -i s/a/b/ src/ofmhelpers/mine.py"}

    answer = hooks.send(
        "PostToolUse", tool_name="Bash", tool_input=sed, duration_ms=300
    )

    assert answer.decision == "allow"


def test_kimi_counts_once_the_cavecrew_reviewer_was_tried(hooks: Hooks) -> None:
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    hooks.edit("src/ofmhelpers/web/x.py", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Bash", tool_input=KIMI)

    assert hooks.send("SubagentStop").decision == "allow"


@pytest.mark.parametrize(
    "last_message",
    [
        "I held off on building the guard. Tell me which route you want: pre-check only, or full hooks.",
        "Both options work. Do you want the scheduled job or a manual script?",
        "The shared guard is drafted. Want me to wire it into clone-links too?",
    ],
)
def test_stopping_to_hand_a_rule_decided_choice_back_is_sent_back_once(
    hooks: Hooks, last_message: str
) -> None:
    answer = hooks.send("SubagentStop", last_assistant_message=last_message)

    assert answer.decision == "block"
    assert "THE RULES DECIDE" in answer.reason
    assert (
        hooks.send(
            "SubagentStop", last_assistant_message=last_message, stop_hook_active=True
        ).decision
        == "allow"
    )


def test_a_finished_report_stops_without_a_push_back(hooks: Hooks) -> None:
    report = "Done: the egress guard refuses private hosts; 14 tests passed, 0 failed. Nothing committed."

    assert hooks.send("Stop", last_assistant_message=report).decision == "allow"


def test_each_owner_prompt_carries_the_claude_md_lead_rules(hooks: Hooks) -> None:
    assert (
        "SHORTEST CODE THAT GETS THE FEATURE WORKING" in hooks.prompt("fix it").context
    )
    assert hooks.prompt("<task-notification>done</task-notification>").context == ""


def test_a_broken_guard_lets_the_call_through_and_says_why(hooks: Hooks) -> None:
    (hooks.root / "scripts" / "check_no_comments.py").unlink()
    (hooks.root / "src" / "ofmhelpers" / "y.py").write_text("y = 2\n")

    answer = hooks.edit("src/ofmhelpers/y.py", when="PostToolUse")

    assert answer.decision == "allow"
    assert "guard no_comments failed, call allowed" in answer.stderr
    assert (hooks.root / "reports" / "hooks" / "errors.log").is_file()


def test_unreadable_input_is_allowed_and_logged(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(DISPATCHER)],
        input="not json",
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_GUARDS_ROOT": str(tmp_path)},
        check=True,
    )

    assert done.stdout == ""
    assert "guard input failed" in done.stderr


def test_an_unwritable_audit_log_still_denies_a_denied_command(hooks: Hooks) -> None:
    (hooks.root / "reports" / "hooks" / "audit.jsonl").mkdir(parents=True)

    answer = hooks.bash("git commit -m x")

    assert answer.decision == "deny"
    assert "never commit" in answer.reason
    assert "guard audit failed, call judged unaudited" in answer.stderr
    errors = (hooks.root / "reports" / "hooks" / "errors.log").read_text()
    assert " audit\nTraceback" in errors


def test_a_refusal_that_cannot_be_printed_still_refuses_by_exit_2(
    hooks: Hooks,
) -> None:
    read_only = hooks.root / "stdout"
    read_only.touch()
    payload = {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "cwd": str(hooks.root),
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m x"},
    }

    with read_only.open("rb") as stdout:
        done = subprocess.run(
            [sys.executable, str(DISPATCHER)],
            input=json.dumps(payload).encode(),
            stdout=stdout,
            stderr=subprocess.PIPE,
            env={
                **os.environ,
                "CLAUDE_GUARDS_ROOT": str(hooks.root),
                "PYDANTIC_DISABLE_PLUGINS": "__all__",
            },
            check=False,
        )

    stderr = done.stderr.decode()
    assert done.returncode == 2
    assert "guard response failed, call refused by exit 2" in stderr
    assert "never commit" in stderr


def test_every_call_leaves_an_audit_line(hooks: Hooks) -> None:
    hooks.bash("git commit -m x")

    lines = (hooks.root / "reports" / "hooks" / "audit.jsonl").read_text().splitlines()
    last = json.loads(lines[-1])
    assert last["verdicts"] == ["git:deny"]
    assert last["target"] == "git commit -m x"


def test_a_deploy_is_followed_by_one_self_learn_pass(hooks: Hooks) -> None:
    merge = {"command": "gh pr merge 120 --merge"}
    hooks.send("PostToolUse", tool_name="Bash", tool_input=merge)

    stop = hooks.send("Stop")
    assert stop.decision == "block"
    assert "self-learn skill" in stop.reason
    assert hooks.send("Stop").decision == "allow"


def test_a_deploy_already_learned_from_ends_quietly(hooks: Hooks) -> None:
    hooks.send(
        "PostToolUse",
        tool_name="Bash",
        tool_input={"command": "gh workflow run deploy.yml --ref main"},
    )
    done = hooks.root / "reports" / "self-learn" / "last.txt"
    done.parent.mkdir(parents=True)
    time.sleep(0.05)
    done.write_text(
        time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 1)),
        encoding="utf-8",
    )

    assert hooks.send("Stop").decision == "allow"


def test_no_deploy_no_self_learn_pass(hooks: Hooks) -> None:
    hooks.send("PostToolUse", tool_name="Bash", tool_input={"command": "gh pr list"})

    assert hooks.send("Stop").decision == "allow"


def test_a_deploy_run_by_a_subagent_still_holds_the_main_session(
    tmp_path: Path,
) -> None:
    hooks_root = Hooks(tmp_path, agent="sub1")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    shutil.copy(REPO / "CLAUDE.md", tmp_path / "CLAUDE.md")
    hooks_root.send(
        "PostToolUse",
        tool_name="Bash",
        tool_input={"command": "gh pr merge 120 --merge"},
    )

    stop = Hooks(tmp_path).send("Stop")

    assert stop.decision == "block"
    assert "self-learn skill" in stop.reason


def test_a_command_that_only_mentions_a_merge_is_no_deploy(hooks: Hooks) -> None:
    text = "cat >> notes.md <<'EOF'\ngh pr merge 120 --merge\nEOF\necho 'gh pr merge 7'"
    hooks.send("PostToolUse", tool_name="Bash", tool_input={"command": text})

    assert hooks.send("Stop").decision == "allow"


def test_a_doc_edit_under_src_needs_no_review(hooks: Hooks) -> None:
    hooks.edit("src/ofmhelpers/integrations/gdrive/CLAUDE.md", when="PostToolUse")

    assert hooks.send("Stop").decision == "allow"


NEW_CSS = "src/ofmhelpers/web/static/css/new.css"
NOTE = (
    "# hooks\n"
    "Pattern in repo: .claude/hooks/guards/review.py Markers\n"
    "Web: https://code.claude.com/docs/en/hooks\n"
    "Skills: senior-python-rules\n"
    "Breaks when: service down or slow: timeout | backend 5xx/HTML/non-JSON: refused |\n"
    "crash after a side effect: replayed | 50 clicks, two tabs: one key |\n"
    "junk input: Pydantic | scale: read from the end | JS crash: no JS here\n"
)


def write_new(hooks: Hooks, relative: str = NEW_CSS) -> Answer:
    write = {"file_path": str(hooks.root / relative), "content": "a {}\n"}
    return hooks.send("PreToolUse", tool_name="Write", tool_input=write)


def researched(hooks: Hooks) -> None:
    for tool, tool_input in (
        ("mcp__codegraph__codegraph_explore", {"query": "Markers review.py"}),
        ("Grep", {"pattern": "idempotency", "path": ".claude/skills"}),
    ):
        hooks.send("PostToolUse", tool_name=tool, tool_input=tool_input)


def write_note(hooks: Hooks, text: str = NOTE, age_s: float = -1.0) -> None:
    note = hooks.root / "reports" / "research" / "hooks.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(text)
    at = time.time() - age_s
    os.utime(note, (at, at))


def test_new_code_waits_for_codegraph_the_skills_search_and_a_note(
    hooks: Hooks,
) -> None:
    assert "Pattern in repo:" in hooks.prompt("build the guard").context

    denied = write_new(hooks)
    assert denied.decision == "deny"
    for missing in ("codegraph_explore", "the skills search", "a note", "Breaks when:"):
        assert missing in denied.reason

    researched(hooks)
    write_note(hooks)
    assert write_new(hooks).decision == "allow"


def test_a_note_must_name_every_failure_mode(hooks: Hooks) -> None:
    hooks.prompt("build the guard")
    researched(hooks)
    write_note(
        hooks, NOTE.replace("scale: read from the end | JS crash: no JS here", "ok")
    )

    denied = write_new(hooks)

    assert denied.decision == "deny"
    assert "naming scale" in denied.reason
    assert "naming JS crash" in denied.reason


def test_no_pattern_in_the_repo_needs_a_web_search_and_its_url(hooks: Hooks) -> None:
    hooks.prompt("build the guard")
    researched(hooks)
    no_pattern = NOTE.replace(
        "Pattern in repo: .claude/hooks/guards/review.py Markers",
        "Pattern in repo: none",
    )
    write_note(hooks, no_pattern.replace("https://code.claude.com/docs/en/hooks", "-"))

    denied = write_new(hooks)
    assert "a web search" in denied.reason
    assert "a URL in `Web:`" in denied.reason

    hooks.send("PostToolUse", tool_name="WebSearch", tool_input={"query": "hooks"})
    write_note(hooks, no_pattern)
    assert write_new(hooks).decision == "allow"


def test_a_note_from_before_the_task_does_not_count(hooks: Hooks) -> None:
    write_note(hooks, age_s=60)
    hooks.prompt("next task")
    researched(hooks)

    assert "a note" in write_new(hooks).reason


def test_a_notification_does_not_restart_the_research(hooks: Hooks) -> None:
    hooks.prompt("build the guard")
    researched(hooks)
    write_note(hooks)

    hooks.prompt("<task-notification><status>completed</status></task-notification>")

    assert write_new(hooks).decision == "allow"


def missing(answer: Answer) -> str:
    return answer.reason.split("Missing since this task started: ")[1].split(".\n")[0]


def test_a_subagent_counts_the_main_sessions_lookups_and_its_own(
    hooks: Hooks,
) -> None:
    hooks.prompt("build the guard")
    subagent = Hooks(hooks.root, session=hooks.session, agent="agent-3")
    started = subagent.send("SubagentStart", agent_type="general-purpose")
    assert "Pattern in repo:" in started.context

    assert missing(write_new(subagent)).startswith(
        "codegraph_explore for the pattern; the skills search; a note"
    )

    researched(hooks)
    assert missing(write_new(subagent)).startswith("a note")

    write_note(hooks)
    assert write_new(subagent).decision == "allow"

    other = Hooks(hooks.root, session="s2")
    other.prompt("build another guard")
    helper = Hooks(hooks.root, session="s2", agent="agent-9")
    helper.send("SubagentStart", agent_type="general-purpose")
    researched(helper)
    write_note(other)
    assert write_new(helper).decision == "allow"


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Grep", {"pattern": "idempotency", "path": ".claude/skills"}),
        (
            "Bash",
            {"command": "git grep -n -i idempotency -- .claude/skills .claude/rules"},
        ),
        ("Skill", {"skill": "failure-modes"}),
    ],
)
def test_the_skills_search_is_a_grep_a_git_grep_or_a_skill_load(
    hooks: Hooks, tool: str, tool_input: dict[str, str]
) -> None:
    hooks.prompt("build the guard")
    codegraph = {"query": "Markers review.py"}
    hooks.send(
        "PostToolUse",
        tool_name="mcp__codegraph__codegraph_explore",
        tool_input=codegraph,
    )
    write_note(hooks)
    assert "the skills search" in missing(write_new(hooks))

    hooks.send("PostToolUse", tool_name=tool, tool_input=tool_input)

    assert write_new(hooks).decision == "allow"


def test_reads_and_globs_wake_no_post_tool_guard_but_grep_skill_and_shell_do() -> None:
    settings = json.loads((REPO / ".claude" / "settings.json").read_text())
    matchers = [
        set(entry["matcher"].split("|"))
        for entry in settings["hooks"]["PostToolUse"]
        if any("guard.py" in hook["command"] for hook in entry["hooks"])
    ]

    assert len(matchers) == 1
    assert not {"Read", "Glob"} & matchers[0]
    assert {
        "Grep",
        "Skill",
        "Bash",
        "PowerShell",
        "TaskStop",
        "WebSearch",
        "WebFetch",
        "mcp__codegraph__codegraph_explore",
    } <= matchers[0]


def test_tests_docs_reports_and_edits_inside_a_function_are_never_blocked(
    hooks: Hooks,
) -> None:
    hooks.prompt("fix the bug")
    hooks.skill("senior-python-rules")
    hooks.skill("failure-modes")
    service = "src/ofmhelpers/features/old/service.py"

    assert hooks.edit(service, old="x = 1", new="x = 2").decision == "allow"
    assert write_new(hooks, "tests/unit/test_new.py").decision == "allow"
    assert write_new(hooks, "docs/new.md").decision == "allow"
    assert write_new(hooks, "reports/research/x.md").decision == "allow"
    added = hooks.edit(service, old="x = 1", new="x = 1\n\n\ndef run():\n    pass")
    assert added.decision == "deny"
    assert "adds new code" in added.reason


def test_a_subagent_gets_the_owners_contract_but_a_reader_does_not(
    hooks: Hooks,
) -> None:
    subagent = Hooks(hooks.root, session=hooks.session, agent="agent-4")

    brief = subagent.send("SubagentStart", agent_type="general-purpose").context
    assert "return it in your report" in brief
    assert "main session runs the review and the worker restart" in brief
    assert subagent.send("SubagentStart", agent_type="Explore").context == ""


def transcript(hooks: Hooks, tokens: int) -> Path:
    usage = {
        "input_tokens": 2,
        "cache_creation_input_tokens": 1000,
        "cache_read_input_tokens": tokens - 1002,
    }
    lines = [
        {"type": "assistant", "message": {"usage": {"input_tokens": 9}}},
        {"type": "assistant", "message": {"usage": usage}},
        {"type": "user", "message": {"content": "x" * 200_000}},
    ]
    path = hooks.root / "transcript.jsonl"
    text = "\n".join(json.dumps(line, separators=(",", ":")) for line in lines)
    path.write_text(text + '\n{"type":"assistant","message":{"usa\n')
    return path


def test_a_big_context_is_told_once_per_200k_of_growth(hooks: Hooks) -> None:
    def prompt(path: Path) -> str:
        return hooks.send(
            "UserPromptSubmit", prompt="next task", transcript_path=str(path)
        ).context

    assert "context is" not in prompt(transcript(hooks, 390_000))
    assert "context is 450k tokens" in prompt(transcript(hooks, 450_000))
    assert "context is" not in prompt(transcript(hooks, 600_000))
    assert "context is 700k tokens" in prompt(transcript(hooks, 700_000))
    assert "context is" not in prompt(hooks.root / "missing.jsonl")

    (hooks.root / "reports" / "hooks" / "ship.open").write_text("x")
    assert "context is" not in prompt(transcript(hooks, 950_000))


def test_a_stopped_run_that_stranded_the_pre_commit_patch_says_apply_it(
    hooks: Hooks, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = hooks.root / "pre-commit-home"
    home.mkdir()
    monkeypatch.setenv("PRE_COMMIT_HOME", str(home))
    tracked = hooks.root / "notes.txt"
    tracked.write_text("one\n")
    subprocess.run(["git", "-C", str(hooks.root), "add", "notes.txt"], check=True)
    tracked.write_text("two\n")
    patch = home / "patch1763700000-42"
    patch.write_bytes(
        subprocess.run(
            ["git", "-C", str(hooks.root), "diff", "--binary"],
            capture_output=True,
            check=True,
        ).stdout
    )
    tracked.write_text("one\n")
    stop = {"task_id": "b7"}

    stranded = hooks.send("PostToolUse", tool_name="TaskStop", tool_input=stop)
    assert stranded.decision == "block"
    assert f'git apply "{patch.as_posix()}"' in stranded.reason

    tracked.write_text("two\n")
    assert (
        hooks.send("PostToolUse", tool_name="TaskStop", tool_input=stop).decision
        == "allow"
    )

    tracked.write_text("one\n")
    three_hours_ago = time.time() - 3 * 3600
    os.utime(patch, (three_hours_ago, three_hours_ago))
    assert (
        hooks.send("PostToolUse", tool_name="TaskStop", tool_input=stop).decision
        == "allow"
    )


def test_only_the_newest_pre_commit_patch_is_checked(
    hooks: Hooks, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = hooks.root / "pre-commit-home"
    home.mkdir()
    monkeypatch.setenv("PRE_COMMIT_HOME", str(home))
    tracked = hooks.root / "notes.txt"
    tracked.write_text("one\n")
    subprocess.run(["git", "-C", str(hooks.root), "add", "notes.txt"], check=True)
    tracked.write_text("two\n")
    applies = home / "patch1763700000-1"
    applies.write_bytes(
        subprocess.run(
            ["git", "-C", str(hooks.root), "diff", "--binary"],
            capture_output=True,
            check=True,
        ).stdout
    )
    tracked.write_text("one\n")
    broken = home / "patch1763700100-2"
    broken.write_text("not a patch\n")
    now = time.time()
    os.utime(applies, (now - 120, now - 120))
    os.utime(broken, (now - 60, now - 60))
    stop = {"task_id": "b7"}

    assert (
        hooks.send("PostToolUse", tool_name="TaskStop", tool_input=stop).decision
        == "allow"
    )

    os.utime(broken, (now - 300, now - 300))
    stranded = hooks.send("PostToolUse", tool_name="TaskStop", tool_input=stop)
    assert stranded.decision == "block"
    assert applies.as_posix() in stranded.reason


def test_a_crashing_git_guard_denies_the_command_instead_of_allowing_it(
    hooks: Hooks,
) -> None:
    status = {"command": "git status"}

    answer = hooks.send(
        "PreToolUse", tool_name="Bash", tool_input=status, cwd="bad\x00dir"
    )

    assert answer.decision == "deny"
    assert "guard git crashed: ValueError" in answer.reason
    assert "fix it before this command" in answer.reason
    assert "guard git failed, call denied" in answer.stderr


def test_the_audit_log_rotates_past_20_mb_keeping_one_generation(
    hooks: Hooks,
) -> None:
    state = hooks.root / "reports" / "hooks"
    state.mkdir(parents=True)
    audit = state / "audit.jsonl"
    over = 20 * 1024 * 1024 + 1
    with audit.open("wb") as log:
        log.truncate(over)
    (state / "audit.jsonl.1").write_text("the generation before\n")

    hooks.bash("git status")

    assert (state / "audit.jsonl.1").stat().st_size == over
    lines = audit.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["target"] == "git status"


def test_a_running_push_suite_blocks_edits_of_tracked_files_only(
    hooks: Hooks,
) -> None:
    (hooks.root / ".gitignore").write_text("/reports/\n")
    (hooks.root / "README.md").write_text("x\n")
    (hooks.root / ".claude").mkdir()
    (hooks.root / ".claude" / "settings.json").write_text("{}\n")
    tracked = ["README.md", ".claude/settings.json"]
    subprocess.run(["git", "-C", str(hooks.root), "add", *tracked], check=True)
    (hooks.root / "reports").mkdir()
    lock = hooks.root / "reports" / ".running"
    lock.write_text("push\n123\n")

    for path in tracked:
        denied = hooks.edit(path)
        assert denied.decision == "deny"
        assert "pre-push suite is running" in denied.reason
    assert hooks.edit("docs/new.md").decision == "allow"
    assert hooks.edit("reports/research/x.md").decision == "allow"

    lock.write_text("files\n123\n")
    assert hooks.edit("README.md").decision == "allow"
    assert "is running" in hooks.edit("src/ofmhelpers/x.py").reason


def committed_tree(root: Path) -> str:
    who = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com"}
    env = {
        **os.environ,
        **who,
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            input="",
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return done.stdout.strip()

    tree = git("mktree")
    git("update-ref", "HEAD", git("commit-tree", tree, "-m", "x"))
    return tree


def test_a_green_gate_reruns_only_on_a_new_tree_or_the_owners_bare_word(
    hooks: Hooks,
) -> None:
    state = hooks.root / "reports" / "hooks"
    state.mkdir(parents=True)
    ship_open = state / "ship.open"
    ship_open.write_text("ed20257")
    os.utime(ship_open, (time.time() - 60, time.time() - 60))
    green = state / "gate.green"
    other_tree = "0" * 40
    green.write_text(f"{other_tree} {int(time.time())}")
    os.utime(green, (time.time() - 120, time.time() - 120))
    assert hooks.bash("scripts/test.sh deploy").decision == "allow"

    os.utime(green, None)
    unknown = hooks.bash("scripts/test.sh deploy")
    assert unknown.decision == "deny"
    assert "whether HEAD is still that tree is unknown" in unknown.reason

    tree = committed_tree(hooks.root)
    green.write_text(f"{tree} {int(time.time())}")
    for preset in ("deploy", "all", "push"):
        denied = hooks.bash(f"scripts/test.sh {preset}")
        assert denied.decision == "deny"
        assert "the gate is green at" in denied.reason
        assert "on this exact tree" in denied.reason
    assert hooks.bash("scripts/test.sh tests/unit/test_x.py").decision == "allow"

    green.write_text(f"{other_tree} {int(time.time())}")
    assert hooks.bash("scripts/test.sh deploy").decision == "allow"

    green.write_text(f"{tree} {int(time.time())}")
    hooks.prompt("rerun the gate")
    assert hooks.bash("scripts/test.sh deploy").decision == "deny"
    hooks.prompt("gate")
    assert hooks.bash("scripts/test.sh deploy").decision == "allow"


def test_an_existing_python_file_is_changed_with_edit_not_rewritten(
    hooks: Hooks,
) -> None:
    def write(relative: str) -> Answer:
        content = {"file_path": str(hooks.root / relative), "content": "x = 2\n"}
        return hooks.send("PreToolUse", tool_name="Write", tool_input=content)

    denied = write("src/ofmhelpers/features/old/service.py")
    assert denied.decision == "deny"
    assert "change it with Edit" in denied.reason
    assert (
        "change it with Edit" not in write("src/ofmhelpers/features/old/new.py").reason
    )
    assert write("notes.txt").decision == "allow"


def _break_a_layer(hooks: Hooks) -> None:
    (hooks.root / "src" / "ofmhelpers" / "db").mkdir()
    (hooks.root / "src" / "ofmhelpers" / "db" / "rows.py").write_text(
        "from ofmhelpers.features.old import service\n"
    )


def test_a_clean_import_graph_lets_the_review_start_and_says_so(hooks: Hooks) -> None:
    started = hooks.send("PreToolUse", tool_name="Agent", tool_input=REVIEWER)

    assert started.decision == "allow"
    assert "0 layer breaks, 0 packages importing each other" in started.context


def test_a_layer_break_stops_the_review_until_it_is_fixed(hooks: Hooks) -> None:
    _break_a_layer(hooks)

    agent = hooks.send("PreToolUse", tool_name="Agent", tool_input=REVIEWER)
    kimi = hooks.send("PreToolUse", tool_name="Bash", tool_input=KIMI)
    windows = {"command": r"python .claude\skills\kimi\ask.py review task.md x"}
    kimi_on_windows = hooks.send(
        "PreToolUse", tool_name="PowerShell", tool_input=windows
    )

    assert agent.decision == "deny"
    assert "layer break: db.rows -> features.old.service" in agent.reason
    assert "layer break: db.rows -> features.old.service" in kimi.reason
    assert "layer break: db.rows -> features.old.service" in kimi_on_windows.reason
    (hooks.root / "src" / "ofmhelpers" / "db" / "rows.py").write_text("x = 1\n")
    assert (
        hooks.send("PreToolUse", tool_name="Agent", tool_input=REVIEWER).decision
        == "allow"
    )


def test_a_kimi_research_call_is_not_a_review(hooks: Hooks) -> None:
    _break_a_layer(hooks)
    research = {"command": 'kimi -p "what changed in rq 2.1"'}

    asked = hooks.send("PreToolUse", tool_name="Bash", tool_input=research)

    assert "layer break" not in asked.reason
    assert "Import graph" not in asked.context


def test_a_kimi_review_with_a_windows_path_counts_as_the_review(hooks: Hooks) -> None:
    windows = {"command": r"python .claude\skills\kimi\ask.py review task.md x"}
    hooks.edit("src/ofmhelpers/web/x.py", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    hooks.edit("src/ofmhelpers/web/x.py", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="PowerShell", tool_input=windows)

    assert hooks.send("Stop").decision == "allow"


def test_a_graph_that_cannot_be_read_stops_the_review(hooks: Hooks) -> None:
    (hooks.root / "src" / "ofmhelpers" / "features" / "old" / "half.py").write_text(
        "def run(:\n"
    )

    agent = hooks.send("PreToolUse", tool_name="Agent", tool_input=REVIEWER)

    assert agent.decision == "deny"
    assert "import_graph_before_review crashed" in agent.reason


def test_the_reviewer_gets_the_graph_and_other_agents_do_not(hooks: Hooks) -> None:
    _break_a_layer(hooks)
    helper = {"description": "Find code", "subagent_type": "general-purpose"}

    reviewer = hooks.send("SubagentStart", agent_type=REVIEWER["subagent_type"])
    other = hooks.send("SubagentStart", agent_type="general-purpose")
    launched = hooks.send("PreToolUse", tool_name="Agent", tool_input=helper)

    assert "layer break: db.rows -> features.old.service" in reviewer.context
    assert "Import graph" not in other.context
    assert "Import graph" not in launched.context + launched.reason
    assert launched.decision == "allow"
