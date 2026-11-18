#!/usr/bin/env bash
# Prints `skip=true` or `skip=false` (a line for $GITHUB_OUTPUT): true when
# CI already passed on exactly this tree, so the push to main need not test
# it again (.github/workflows/ci.yml `gate`).
#
#   scripts/ci_reuse_dev_run.sh [<commit>]     default HEAD; needs `gh` + GH_TOKEN
#
# The tree counts as tested when the CI workflow's push run on dev succeeded on
#   - the commit itself (a fast-forward of main to a tested dev commit), or
#   - its second parent, when the merge changed nothing: the promote PR's
#     merge commit has the dev head's tree, byte for byte.
# A commit only on main (a hotfix), a squash, or a merge that also brings in
# main's own changes has a tree no CI run saw: full CI.
set -euo pipefail

commit=$(git rev-parse "${1:-HEAD}")
repo=${GITHUB_REPOSITORY:?set GITHUB_REPOSITORY (owner/name)}

passed() {
  # security audit 2026-11-18: only ci.yml's own push run on dev counts, not
  # any check run that happens to be named "CI passed".
  local count
  count=$(gh api "repos/$repo/actions/runs?head_sha=$1&event=push&branch=dev&status=success&per_page=100"     --jq '[.workflow_runs[] | select(.path == ".github/workflows/ci.yml" and .conclusion == "success" and .event == "push" and .head_branch == "dev")] | length') || return 1
  [ "${count:-0}" -gt 0 ]
}

candidates=("$commit")
if second=$(git rev-parse -q --verify "$commit^2"); then
  if [ "$(git rev-parse "$commit^{tree}")" = "$(git rev-parse "$second^{tree}")" ]; then
    candidates+=("$second")
  fi
fi

for sha in "${candidates[@]}"; do
  if passed "$sha"; then
    echo "tested tree: CI passed on $sha" >&2
    echo "skip=true"
    exit 0
  fi
done
echo "untested tree: full CI" >&2
echo "skip=false"
