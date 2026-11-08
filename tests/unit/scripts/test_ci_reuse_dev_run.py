"""scripts/ci_reuse_dev_run.sh: main's CI skips the suite only for a tree a
green CI run already tested.

Each test builds its own git history in tmp_path and puts a fake `gh` first
on PATH that answers "CI passed" for the commits listed in GREEN.
"""

import os
import subprocess
from pathlib import Path

import pytest
from repo_paths import REPO

SCRIPT = REPO / "scripts/ci_reuse_dev_run.sh"

# The URL is the 2nd argument: repos/<repo>/commits/<sha>/check-runs?...
_FAKE_GH = """#!/usr/bin/env bash
[ -f "$FAKE_GH_FAIL" ] && exit 1
sha=$(echo "$2" | cut -d/ -f5)
grep -qx "$sha" "$GREEN" && echo 1 || echo 0
"""


class Repo:
    def __init__(self, root: Path):
        self.root = root
        self.green = root.parent / "green.txt"
        self.green.write_text("", encoding="utf-8")
        self.fail = root.parent / "gh-fails"
        bin_dir = root.parent / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(_FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "GREEN": str(self.green),
            "FAKE_GH_FAIL": str(self.fail),
            "GITHUB_REPOSITORY": "owner/repo",
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        root.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def commit(self, name: str, text: str) -> str:
        (self.root / name).write_text(text, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", f"{name}: {text}")
        return self.git("rev-parse", "HEAD")

    def mark_green(self, sha: str) -> None:
        with self.green.open("a", encoding="utf-8") as f:
            f.write(sha + "\n")

    def gate(self) -> str:
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Repo:
    r = Repo(tmp_path / "repo")
    r.commit("app.py", "v1")
    r.git("checkout", "-q", "-b", "dev")
    return r


def _promote(repo: Repo) -> str:
    """The promote PR's merge: a merge commit of dev into main."""
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge dev", "dev")
    return repo.git("rev-parse", "HEAD")


def test_a_merge_of_a_tested_dev_head_skips(repo):
    repo.mark_green(repo.commit("app.py", "v2"))
    _promote(repo)
    assert repo.gate() == "skip=true"


def test_a_merge_of_an_untested_dev_head_runs_the_suite(repo):
    repo.commit("app.py", "v2")
    _promote(repo)
    assert repo.gate() == "skip=false"


def test_a_merge_that_also_carries_a_main_hotfix_runs_the_suite(repo):
    repo.mark_green(repo.commit("app.py", "v2"))
    repo.git("checkout", "-q", "main")
    repo.commit("hotfix.py", "only on main")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge dev", "dev")
    assert repo.gate() == "skip=false"


def test_a_fast_forward_to_a_tested_commit_skips(repo):
    repo.mark_green(repo.commit("app.py", "v2"))
    repo.git("checkout", "-q", "main")
    repo.git("merge", "-q", "--ff-only", "dev")
    assert repo.gate() == "skip=true"


def test_a_failing_lookup_runs_the_suite(repo):
    repo.mark_green(repo.commit("app.py", "v2"))
    _promote(repo)
    repo.fail.touch()
    assert repo.gate() == "skip=false"
