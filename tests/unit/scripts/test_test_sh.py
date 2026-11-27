# Copyright (C) 2026 jpkrajewski

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from repo_paths import REPO

FAKE_DOCKER = """#!/usr/bin/env bash
case "$*" in
  *affected_tests.py*)
    [ -f "$FAKE_SELECTOR_FAILS" ] && { echo "Traceback: selector" >&2; exit 3; }
    printf '%s\n' "$@" > "$FAKE_SELECTOR_ARGS"
    echo tests/unit/x/test_a.py
    echo "tests/unit/with space/test_b.py"
    ;;
  *test_summary.py*) echo "RESULT $FAKE_RESULT" ;;
  *pytest*) echo "ran pytest $*"; exit "$FAKE_PYTEST_EXIT" ;;
  *) ;;
esac
"""


class Project:
    def __init__(self, root: Path):
        self.root = root
        bin_dir = root.parent / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        self.selector_fails = root.parent / "selector-fails"
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_SELECTOR_FAILS": str(self.selector_fails),
            "FAKE_SELECTOR_ARGS": str(root.parent / "selector-args"),
            "FAKE_RESULT": "GREEN",
            "FAKE_PYTEST_EXIT": "0",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        (root / "scripts").mkdir(parents=True)
        shutil.copy(REPO / "scripts/test.sh", root / "scripts/test.sh")
        (root / ".gitignore").write_text("reports/\n", encoding="utf-8")
        (root / "app.py").write_text("v1", encoding="utf-8")
        self.git("init", "-q", "-b", "dev")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "first")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "scripts/test.sh", *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )

    @property
    def gate_green(self) -> Path:
        return self.root / "reports/hooks/gate.green"


@pytest.fixture
def project(tmp_path) -> Project:
    root = tmp_path / "project"
    root.mkdir()
    return Project(root)


def test_a_bare_green_deploy_on_a_clean_tree_writes_the_gate(project):
    result = project.run("deploy")
    assert result.returncode == 0, result.stderr
    assert project.gate_green.read_text(encoding="utf-8").split()[0] == project.git(
        "rev-parse",
        "HEAD^{tree}",
    )


@pytest.mark.parametrize("extra", [["tests/x.py"], ["-k", "name"]])
def test_a_deploy_with_extra_arguments_never_writes_the_gate(project, extra):
    result = project.run("deploy", *extra)
    assert result.returncode == 0, result.stderr
    assert not project.gate_green.exists()


def test_a_red_deploy_never_writes_the_gate(project):
    project.env["FAKE_PYTEST_EXIT"] = "1"
    assert project.run("deploy").returncode == 1
    assert not project.gate_green.exists()


def test_a_deploy_started_on_a_dirty_tree_never_writes_the_gate(project):
    (project.root / "app.py").write_text("v2", encoding="utf-8")
    assert project.run("deploy").returncode == 0
    assert not project.gate_green.exists()


def test_push_is_skipped_only_on_the_tree_the_gate_passed_and_clean(project):
    assert project.run("deploy").returncode == 0
    skipped = project.run("push")
    assert skipped.returncode == 0
    assert "push skipped" in skipped.stdout
    assert "ran pytest" not in skipped.stdout

    with_arguments = project.run("push", "tests/x.py")
    assert "push skipped" not in with_arguments.stdout
    assert "ran pytest" in with_arguments.stdout

    (project.root / "app.py").write_text("v2", encoding="utf-8")
    dirty = project.run("push")
    assert "push skipped" not in dirty.stdout
    assert "ran pytest" in dirty.stdout

    project.git("commit", "-q", "-am", "second")
    other_tree = project.run("push")
    assert "push skipped" not in other_tree.stdout
    assert "ran pytest" in other_tree.stdout


def test_affected_runs_what_the_selector_names_paths_with_spaces_included(project):
    (project.root / "my file.py").write_text("x", encoding="utf-8")
    result = project.run("affected")
    assert result.returncode == 0, result.stderr
    assert "affected: 2 test files" in result.stdout
    assert "ran pytest" in result.stdout
    arguments = Path(project.env["FAKE_SELECTOR_ARGS"]).read_text(encoding="utf-8")
    assert "my file.py" in arguments.splitlines()
    assert "tests/unit/x/test_a.py" in result.stdout
    assert "tests/unit/with space/test_b.py" in result.stdout


def test_affected_runs_the_heavy_tests_of_the_files_it_picks(project):
    (project.root / "app.py").write_text("v2", encoding="utf-8")
    result = project.run("affected")
    assert result.returncode == 0, result.stderr
    assert "-m not e2e and not live and not snapshot and not paid" in result.stdout
    assert "not heavy" not in result.stdout


def test_affected_fails_when_the_selector_crashes(project):
    project.selector_fails.touch()
    result = project.run("affected")
    assert result.returncode == 1
    assert "selector crashed" in result.stderr
    assert "Traceback: selector" in result.stderr
    assert "no test belongs" not in result.stdout
    assert "ran pytest" not in result.stdout
