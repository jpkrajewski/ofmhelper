import os
import re
import shlex
import subprocess
from pathlib import Path

from pydantic import BaseModel

from guards.event import ROOT, Guard, HookEvent, Verdict, deny
from guards.shell import program_name, shell_statements

NAME = "pr_comments"
GH_TIMEOUT_S = 8
GH_VALUE_FLAGS = frozenset({"--repo", "--hostname"})
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
CANONICAL = re.compile(r"(?:rtk\s+)?gh\s+pr\s+merge\s+(\d+)\s+--merge")
HAS_COMMENTS = (
    "PR #{number} has {count} open review comment(s): an unresolved review thread or a "
    'change request (CodeQL alerts count). Owner 2026-11-20: "YOU CANT MERGE IF THERE ARE '
    'COMMENTS". Close each one first (ship skill, step 6): verify it against the code, then '
    "fix it on dev with its scoped tests (pre-push and CI rerun the suite), or reply with the "
    "reason and the evidence, resolve "
    "the thread and dismiss the CodeQL alert. Merge once none is open."
)
TOO_MANY = (
    "PR #{number} has more than 100 review threads or reviews, more than one read "
    "covers, so it is not merged. Look by hand: gh pr view {number} --comments."
)
NOT_CANONICAL = (
    "Use the canonical form: gh pr merge <n> --merge, alone in the command and run from the repo "
    "root ({root}). Every other spelling (gh.exe or a path, -R, a URL, GH_REPO, a pipe, cd, "
    "another command, extra flags, gh api) is refused: the review-comment check reads only "
    "that form."
)
UNREADABLE = (
    "Could not read the review comments of PR #{number} ({error}), so it is not merged. "
    "Look by hand: gh pr view {number} --comments, and the PR's Files tab."
)


class Comments(BaseModel, frozen=True):
    open: int
    more: bool


def gh() -> list[str]:
    return shlex.split(os.environ.get("CLAUDE_GUARDS_GH") or "gh")


def takes_value(flag: str) -> bool:
    short_cluster = not flag.startswith("--") and flag.endswith("r")
    return flag in GH_VALUE_FLAGS or short_cluster


def positional(words: list[str]) -> list[str]:
    found: list[str] = []
    skip = False
    for word in words:
        if skip:
            skip = False
        elif word.startswith("-"):
            skip = takes_value(word)
        else:
            found.append(word)
    return found


def merges(words: list[str]) -> bool:
    if not words or program_name(words[0]) != "gh":
        return False
    rest = [word.lower() for word in words[1:]]
    if positional(rest)[:2] == ["pr", "merge"]:
        return True
    return "api" in rest and any(
        word.rstrip("/").endswith("/merge") or "mergepullrequest" in word
        for word in rest
    )


def canonical_number(event: HookEvent) -> str:
    found = CANONICAL.fullmatch(event.command.strip())
    from_root = Path(event.cwd or ROOT).resolve() == ROOT.resolve()
    return found[1] if found and from_root else ""


def open_comments(number: str) -> Comments:
    done = subprocess.run(
        [
            *gh(),
            "api",
            "graphql",
            "-F",
            "owner={owner}",
            "-F",
            "repo={repo}",
            "-F",
            f"number={number}",
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


def judge(number: str) -> Verdict | None:
    try:
        comments = open_comments(number)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        return deny(NAME, UNREADABLE.format(number=number, error=str(exc)[:200]))
    if comments.more:
        return deny(NAME, TOO_MANY.format(number=number))
    if comments.open:
        return deny(NAME, HAS_COMMENTS.format(number=number, count=comments.open))
    return None


def check(event: HookEvent) -> Verdict | None:
    attempts = any(
        merges(words)
        for statement in shell_statements(event)
        for words in (statement.words, *statement.piped_to)
    )
    if not attempts:
        return None
    number = canonical_number(event)
    return judge(number) if number else deny(NAME, NOT_CANONICAL.format(root=ROOT))


GUARD = Guard(
    name=NAME,
    events=frozenset({"PreToolUse"}),
    tools=frozenset({"Bash", "PowerShell"}),
    check=check,
)
