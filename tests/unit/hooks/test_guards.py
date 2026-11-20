import json
import os
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


@pytest.fixture
def hooks(tmp_path: Path) -> Hooks:
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO / "scripts" / "check_no_comments.py", tmp_path / "scripts")
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
        ("git stash list", "Never git stash"),
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
    ],
)
def test_an_ordinary_command_passes(hooks: Hooks, command: str) -> None:
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

    hooks.prompt("<task-notification><status>completed</status></task-notification>")
    assert hooks.bash("git commit -m fix").decision == "allow"

    hooks.prompt("now fix the button")
    assert hooks.bash("git commit -m fix").decision == "deny"


def test_a_subagent_never_inherits_the_owners_word(hooks: Hooks) -> None:
    hooks.prompt("ship")
    subagent = Hooks(hooks.root, session=hooks.session, agent="agent-7")

    assert subagent.bash("git commit -m x").decision == "deny"


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

    denied = hooks.edit(router)
    assert denied.decision == "deny"
    assert "senior-python-rules, fastapi, rest-api" in denied.reason

    for name in ("anthropic-skills:senior-python-rules", "fastapi", "rest-api"):
        hooks.skill(name)
    assert hooks.edit(router).decision == "allow"


def test_architecture_is_needed_for_a_new_feature_file_not_an_existing_one(
    hooks: Hooks,
) -> None:
    hooks.skill("senior-python-rules")

    assert "architecture" in hooks.edit("src/ofmhelpers/features/new/service.py").reason
    assert hooks.edit("src/ofmhelpers/features/old/service.py").decision == "allow"


def test_skills_loaded_by_one_agent_do_not_count_for_another(hooks: Hooks) -> None:
    hooks.skill("senior-python-rules")
    other = Hooks(hooks.root, session=hooks.session, agent="agent-2")

    assert other.edit("tests/unit/test_x.py").decision == "deny"


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
    hooks.edit("src/ofmhelpers/features/repurpose/tasks.py", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)

    stop = hooks.send("SubagentStop")
    assert stop.decision == "block"
    assert "restart worker media-worker" in stop.reason

    hooks.edit("src/ofmhelpers/features/repurpose/tasks.py", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    restart = {"command": f"{DEV_COMPOSE} restart worker media-worker sweeps"}
    hooks.send("PostToolUse", tool_name="Bash", tool_input=restart)
    assert hooks.send("SubagentStop").decision == "allow"


def test_a_doc_beside_worker_code_needs_no_worker_restart(hooks: Hooks) -> None:
    hooks.edit("src/ofmhelpers/media/repurpose/CLAUDE.md", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)

    assert hooks.send("SubagentStop").decision == "allow"


def test_a_web_only_change_needs_no_worker_restart(hooks: Hooks) -> None:
    hooks.edit("src/ofmhelpers/web/templates/x.html", when="PostToolUse")
    hooks.send("PostToolUse", tool_name="Agent", tool_input=REVIEWER)
    shot = {"command": "uv run python -c 'import ui_screens as u; u.shoot(p, s, out)'"}
    hooks.send("PostToolUse", tool_name="Bash", tool_input=shot)

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
