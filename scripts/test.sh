#!/usr/bin/env bash
# Runs the test suite in the Docker `tests` service, with a report every time.
#
#   scripts/test.sh              unit + integration + contract (fast, offline, parallel)
#   scripts/test.sh e2e          e2e only (its own app, every outside service faked)
#   scripts/test.sh all          unit + integration + contract + e2e
#   scripts/test.sh live         live only: REAL Drive sandbox / providers / proxies
#   scripts/test.sh deploy       all + live: the gate before a deploy
#   scripts/test.sh snapshot     migrations on a copy of the prod snapshot
#   scripts/test.sh <args...>    anything else goes straight to pytest
#
# Every run writes:
#   reports/<timestamp>/output.log   the full output (also shown live)
#   reports/<timestamp>/report.json  pytest-json-report
#   reports/latest/                  a copy of the newest run's folder
#   reports/allure-results/          raw Allure results (previous history kept)
#   reports/allure-report/           the generated Allure report (scripts/report.sh)
set -uo pipefail

cd "$(dirname "$0")/.."

compose=(docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile test)
# Parallel, one test database per xdist worker; every e2e test goes to one
# worker (xdist_group "e2e", tests/conftest.py), which starts the one app.
parallel=(-n auto --maxprocesses=8 --dist loadgroup)

case "${1:-}" in
    "") args=("${parallel[@]}") ;;
    e2e) shift; args=(-m e2e "$@") ;;
    all) shift; args=(-m "not live and not snapshot" "${parallel[@]}" "$@") ;;
    live) shift; args=(-m live "$@") ;;
    deploy) shift; args=(-m "not snapshot" "${parallel[@]}" "$@") ;;
    snapshot) shift; args=(-m snapshot "$@") ;;
    *) args=("$@") ;;
esac

stamp="$(date +%Y%m%d-%H%M%S)"
run_dir="reports/$stamp"
mkdir -p "$run_dir"

# Allure trends and flaky detection need the previous report's history.
rm -rf reports/allure-results
mkdir -p reports/allure-results
if [[ -d reports/allure-report/history ]]; then
    cp -r reports/allure-report/history reports/allure-results/history
fi

echo "test.sh: pytest ${args[*]} -> $run_dir"
start=$(date +%s)
"${compose[@]}" run --rm tests uv run --frozen pytest \
    --alluredir=reports/allure-results \
    --json-report --json-report-file="$run_dir/report.json" --json-report-omit=log \
    --durations=20 \
    "${args[@]}" 2>&1 | tee "$run_dir/output.log"
status=${PIPESTATUS[0]}
echo "test.sh: pytest exit $status in $(( $(date +%s) - start ))s" | tee -a "$run_dir/output.log"

# Generate the report now so the history moves on with every run.
"${compose[@]}" run --rm --no-deps -T allure generate --clean \
    -o reports/allure-report reports/allure-results > "$run_dir/allure.log" 2>&1 \
    || echo "test.sh: allure generate failed, see $run_dir/allure.log"

# A plain copy, not a symlink: Windows hosts do not follow WSL symlinks.
rm -rf reports/latest
cp -r "$run_dir" reports/latest

echo "test.sh: report -> reports/latest/report.json, open with scripts/report.sh"
exit "$status"
