#!/usr/bin/env bash
set -uo pipefail
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/.." || exit 1
compose=(docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile test)
work=reports/.quality-work
rm -rf "$work"
mkdir -p "$work" reports/quality
trap 'rm -rf "$work"' EXIT
failed=0

run_json() {
    local name="$1"
    shift
    "${compose[@]}" run --rm --no-deps -T tests "$@" > "$work/$name.json" 2> "$work/$name.log"
    printf '%s\n' "$?" > "$work/$name.status"
}

run_text() {
    local name="$1"
    shift
    "${compose[@]}" run --rm --no-deps -T tests "$@" > "$work/$name.log" 2>&1
    printf '%s\n' "$?" > "$work/$name.status"
}

run_json radon_cc uv run --frozen radon cc --json src tests alembic
run_json radon_raw uv run --frozen radon raw --json src tests alembic
run_json radon_mi uv run --frozen radon mi --json src tests alembic
run_text xenon uv run --frozen xenon --max-absolute F --max-modules C --max-average A --paths-in-front src tests alembic
run_json pylint uv run --frozen pylint --disable=all --enable=duplicate-code --recursive=y --output-format=json --score=n src tests alembic
run_text import_linter uv run --frozen lint-imports --no-cache
run_text custom_metrics uv run --frozen python scripts/quality_metrics.py . "$work/radon_cc.json" "$work" "$work/radon_mi.json"

for name in radon_cc radon_raw radon_mi xenon import_linter custom_metrics; do
    if [[ "$(cat "$work/$name.status")" -ne 0 ]]; then
        failed=1
    fi
done
pylint_status="$(cat "$work/pylint.status")"
if [[ "$pylint_status" -ne 0 && "$pylint_status" -ne 8 ]]; then
    failed=1
fi

if ! "${compose[@]}" run --rm --no-deps -T tests uv run --frozen python scripts/quality_metrics.py combine "$work" > reports/quality/results.json 2> "$work/combine.log"; then
    failed=1
fi
if [[ ! -s reports/quality/results.json ]]; then
    printf '%s\n' '{"passed":false,"error":"could not combine quality results"}' > reports/quality/results.json
fi
cat reports/quality/results.json
exit "$failed"
