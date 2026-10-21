#!/usr/bin/env bash
# Opens the newest Allure report (from scripts/test.sh) in the browser, served
# from Docker -- no Java (or anything else) on this machine.
#
#   scripts/report.sh          serve on http://127.0.0.1:5050 and open it
#   scripts/report.sh stop     stop the server
set -euo pipefail

cd "$(dirname "$0")/.."

compose=(docker compose -f docker-compose.yml -f docker-compose.dev.yml --profile test)
name=ofmhelpers-allure-report
url=http://127.0.0.1:5050

docker rm -f "$name" > /dev/null 2>&1 || true
if [[ "${1:-}" == "stop" ]]; then
    exit 0
fi

if [[ ! -d reports/allure-results ]]; then
    echo "report.sh: no reports/allure-results yet -- run scripts/test.sh first" >&2
    exit 1
fi
"${compose[@]}" run --rm --no-deps -T allure generate --clean \
    -o reports/allure-report reports/allure-results > /dev/null
# Static files: `allure open` refuses to bind anything but loopback inside
# its container ("intended for local report preview only").
"${compose[@]}" run -d --no-deps -p 127.0.0.1:5050:5050 --name "$name" tests \
    python -m http.server 5050 --bind 0.0.0.0 --directory reports/allure-report > /dev/null

for _ in $(seq 1 50); do
    curl -fs -o /dev/null "$url" && break
    sleep 0.2
done
case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*) cmd.exe //c start "" "$url" ;;
    Darwin) open "$url" ;;
    *) xdg-open "$url" > /dev/null 2>&1 || true ;;
esac
echo "report.sh: Allure report at $url (scripts/report.sh stop to stop it)"
