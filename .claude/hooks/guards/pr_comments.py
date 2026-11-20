import os
import re
import shlex
import subprocess
from pathlib import PurePosixPath

from pydantic import BaseModel

from guards.event import ROOT, Guard, HookEvent, Verdict, deny
from guards.shell import shell_statements

NAME = "pr_comments"
GH_TIMEOUT_S = 8
OPEN_COMMENTS = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) { nodes { isResolved } pageInfo { hasNextPage } }
      latestReviews(first: 100) { nodes { state } pageInfo { hasNextPage } }
    }
  }
}
"""
COUNT = (
    ".data.repository.pullRequest as $pr | {open: "
    "(([$pr.reviewThreads.nodes[] | select(.isResolved | not)] | length)"
    ' + ([$pr.latestReviews.nodes[] | select(.state == "CHANGES_REQUESTED")] | length)),'
    " more: ($pr.reviewThreads.pageInfo.hasNextPage"
    " or $pr.latestReviews.pageInfo.hasNextPage)}"
)
PR_URL = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)")
REPO_FLAGS = frozenset({"-R", "--repo"})
VALUE_FLAGS = REPO_FLAGS | {
    "-b",
    "--body",
    "-F",
    "--body-file",
    "-t",
    "--subject",
    "-A",
    "--author-email",
    "--match-head-commit",
    "--hostname",
}
HAS_COMMENTS = (
    "PR #{number} has {count} open review comment(s): an unresolved review thread or a "
    'change request (CodeQL alerts count). Owner 2026-11-20: "YOU CANT MERGE IF THERE ARE '
    'COMMENTS". Close each one first (ship skill, step 6): verify it against the code, then '
    "fix it on dev and rerun the gate, or reply with the reason and the evidence, resolve "
    "the thread and dismiss the CodeQL alert. Merge once none is open."
)
TOO_MANY = (
    "PR #{number} has more than 100 review threads or reviews, more than one read "
    "covers, so it is not merged. Look by hand: gh pr view {number} --comments."
)
NO_NUMBER = (
    "Name the PR: gh pr merge <number>. The review-comment check needs the number."
)
ONE_MERGE = (
    "One gh pr merge per command, so each PR's review comments are read in time."
)
NO_GH_REPO = (
    "Name the repo with -R owner/repo, not GH_REPO: the review-comment check reads -R."
)
UNREADABLE = (
    "Could not read the review comments of PR #{number} ({error}), so it is not merged. "
    "Look by hand: gh pr view {number} --comments, and the PR's Files tab."
)


class Target(BaseModel, frozen=True):
    number: str
    owner: str = "{owner}"
    repo: str = "{repo}"


class Comments(BaseModel, frozen=True):
    open: int
    more: bool


def gh() -> list[str]:
    return shlex.split(os.environ.get("CLAUDE_GUARDS_GH") or "gh")


def _program(word: str) -> str:
    return PurePosixPath(word.replace("\\", "/")).name.removesuffix(".exe")


def _flag(word: str) -> tuple[str, str | None]:
    if word.startswith("--"):
        flag, has_value, value = word.partition("=")
        return flag, value if has_value else None
    for index, letter in enumerate(word[1:], start=2):
        if f"-{letter}" in VALUE_FLAGS:
            return f"-{letter}", word[index:].removeprefix("=") or None
    return word, None


def _arguments(words: list[str]) -> tuple[list[str], str]:
    arguments: list[str] = []
    repo = ""
    index = 0
    while index < len(words):
        word = words[index]
        flag, value = _flag(word) if word.startswith("-") else ("", None)
        if flag in VALUE_FLAGS:
            if value is None:
                value = words[index + 1] if index + 1 < len(words) else ""
                index += 1
            repo = value if flag in REPO_FLAGS else repo
        elif not flag:
            arguments.append(word)
        index += 1
    return arguments, repo


def merge_target(words: list[str]) -> Target | None:
    if not words or _program(words[0].lower()) != "gh":
        return None
    arguments, repo = _arguments(words[1:])
    if arguments[:2] != ["pr", "merge"]:
        return None
    picked = next(iter(arguments[2:]), "")
    if found := PR_URL.search(picked):
        return Target(number=found[3], owner=found[1], repo=found[2])
    number = picked.rsplit("/", 1)[-1].lstrip("#")
    owner, _, name = repo.rpartition("/")
    owner = owner.rpartition("/")[2]
    if owner and name:
        return Target(number=number, owner=owner, repo=name)
    return Target(number=number)


def _field(name: str, value: str) -> list[str]:
    return ["-F" if value.startswith("{") else "-f", f"{name}={value}"]


def open_comments(target: Target) -> Comments:
    done = subprocess.run(
        [
            *gh(),
            "api",
            "graphql",
            *_field("owner", target.owner),
            *_field("repo", target.repo),
            "-F",
            f"number={target.number}",
            "-f",
            f"query={OPEN_COMMENTS}",
            "--jq",
            COUNT,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=GH_TIMEOUT_S,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"gh exited {done.returncode}")
    return Comments.model_validate_json(done.stdout)


def judge(target: Target) -> Verdict | None:
    number = target.number
    if not number.isdigit():
        return deny(NAME, NO_NUMBER)
    try:
        comments = open_comments(target)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return deny(NAME, UNREADABLE.format(number=number, error=str(exc)[:200]))
    if comments.more:
        return deny(NAME, TOO_MANY.format(number=number))
    if comments.open:
        return deny(NAME, HAS_COMMENTS.format(number=number, count=comments.open))
    return None


def check(event: HookEvent) -> Verdict | None:
    merges = [
        target
        for statement in shell_statements(event)
        for words in (statement.words, *statement.piped_to)
        if (target := merge_target(words)) is not None
    ]
    if not merges:
        return None
    if len(merges) > 1:
        return deny(NAME, ONE_MERGE)
    if "GH_REPO" in event.command:
        return deny(NAME, NO_GH_REPO)
    return judge(merges[0])


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse"}),
    tools=frozenset({"Bash", "PowerShell"}),
    check=check,
)
