#!/usr/bin/env bash
# Runs the test suite in the Docker `tests` service, with a report every time.
#
#   scripts/test.sh              unit + integration + contract (fast, offline, parallel)
#   scripts/test.sh e2e          e2e only (its own app, every outside service faked)
#   scripts/test.sh all          unit + integration + contract + e2e (no heavy)
#   scripts/test.sh push         default + heavy (ffmpeg): what CI runs on push
#   scripts/test.sh live         live only: REAL Drive sandbox / providers / proxies (free)
#   scripts/test.sh deploy       all + heavy + live: the gate before a deploy (free)
#   scripts/test.sh paid         the real provider jobs that cost money -- by hand only
#   scripts/test.sh snapshot     migrations on a copy of the prod snapshot
#   scripts/test.sh durations    a `push` run, then .test_durations from it
#                                (CI's three test groups split by these times)
#   scripts/test.sh <args...>    anything else goes straight to pytest
#
# Every run writes:
#   reports/<timestamp>/output.log   the full output (also shown live)
#   reports/<timestamp>/report.json  pytest-json-report
#   reports/latest/                  a copy of the newest run's folder
#   volume test_allure               raw Allure results (previous history kept);
#                                    not the Windows bind mount, which doubled
#                                    the run (docker-compose.dev.yml)
#   reports/allure-report/           the generated Allure report (scripts/report.sh)
set -uo pipefail
# Git Bash rewrites /container/paths into C:/... (a stray `C:` folder of
# results in the checkout, 2026-11-08); every path here is the container's.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.." || exit 1

compose=(docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile test)
# Parallel, one test database per xdist worker; every e2e test goes to one
# worker (xdist_group "e2e", tests/conftest.py), which starts the one app.
parallel=(-n auto --maxprocesses=8 --dist loadgroup)

case "${1:-}" in
    "") args=("${parallel[@]}") ;;
    e2e) shift; args=(-m e2e "$@") ;;
    all) shift; args=(-m "not live and not snapshot and not heavy and not paid" "${parallel[@]}" "$@") ;;
    push) shift; args=(-m "not e2e and not live and not snapshot and not paid" "${parallel[@]}" "$@") ;;
    live) shift; args=(-m "live and not paid" "$@") ;;
    deploy) shift; args=(-m "not snapshot and not paid" "${parallel[@]}" "$@") ;;
    paid) shift; args=(-m paid "$@") ;;
    snapshot) shift; args=(-m snapshot "$@") ;;
    durations) shift; args=(-m "not e2e and not live and not snapshot and not paid" "${parallel[@]}" "$@"); store_durations=1 ;;
    *) args=("$@") ;;
esac

stamp="$(date +%Y%m%d-%H%M%S)"
run_dir="reports/$stamp"
mkdir -p "$run_dir"

# The results volume starts empty but for the previous report's history
# (Allure trends and flaky detection). Inside the container: the volume is
# only there. The old bind-mounted results folder goes once.
rm -rf reports/allure-results
# shellcheck disable=SC2016  # $@ is the container shell's
prepare='find /allure-results -mindepth 1 -delete && if [ -d reports/allure-report/history ]; then cp -r reports/allure-report/history /allure-results/history; fi && exec "$@"'

echo "test.sh: pytest ${args[*]} -> $run_dir"
start=$(date +%s)
"${compose[@]}" run --rm tests sh -c "$prepare" sh uv run --frozen pytest \
    --alluredir=/allure-results \
    --json-report --json-report-file="$run_dir/report.json" --json-report-omit=log \
    --durations=20 \
    "${args[@]}" 2>&1 | tee "$run_dir/output.log"
status=${PIPESTATUS[0]}
echo "test.sh: pytest exit $status in $(( $(date +%s) - start ))s" | tee -a "$run_dir/output.log"

# Generate the report now so the history moves on with every run.
"${compose[@]}" run --rm --no-deps -T allure generate --clean \
    -o reports/allure-report /allure-results > "$run_dir/allure.log" 2>&1 \
    || echo "test.sh: allure generate failed, see $run_dir/allure.log"

# A plain copy, not a symlink: Windows hosts do not follow WSL symlinks.
rm -rf reports/latest
cp -r "$run_dir" reports/latest

if [[ -n "${store_durations:-}" && "$status" -eq 0 ]]; then
    "${compose[@]}" run --rm --no-deps -T tests uv run --frozen python scripts/test_durations.py \
        || status=1
fi

echo "test.sh: report -> reports/latest/report.json, open with scripts/report.sh"
exit "$status"
