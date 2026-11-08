#!/usr/bin/env bash
# Production data into the dev database -- what every fix is checked on
# (CLAUDE.md "Validation"). Read-only on the server: one pg_dump, its file
# removed again; nothing of production changes.
#
#   scripts/prod_to_dev.sh          dump prod, load it into dev, start dev
#   scripts/prod_to_dev.sh --reuse  load the tests/fixtures/prod_snapshot.sql
#                                   already here (no ssh)
#
# Prints the migration log, /health, and every table whose row count differs
# between prod (counted right after the dump) and dev. A few rows of drift on
# busy tables (clicks, events) is prod moving on, not a bad load.
#
# Server details come from deploy.local.env (gitignored), like
# scripts/deploy.sh; see deploy.local.env.example.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f deploy.local.env ]; then
  set -a
  # shellcheck disable=SC1091
  source deploy.local.env
  set +a
fi

compose=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)
snapshot=tests/fixtures/prod_snapshot.sql
# Everything that writes to the database: stopped while it is replaced.
writers=(ofmhelpers worker clone-links-worker preview-worker media-worker edit-worker events-worker)
psql_dev=("${compose[@]}" exec -T postgres psql -U ofmhelpers -v ON_ERROR_STOP=1)

# Exact row count of every table, `table|rows`, sorted.
counts_sql="SELECT table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"

prod_counts=""
if [ "${1:-}" != "--reuse" ]; then
  : "${OFM_DEPLOY_HOST:?set OFM_DEPLOY_HOST -- see deploy.local.env.example}"
  : "${OFM_DEPLOY_DIR:?set OFM_DEPLOY_DIR -- see deploy.local.env.example}"
  : "${OFM_DEPLOY_SSH_KEY:?set OFM_DEPLOY_SSH_KEY -- see deploy.local.env.example}"
  remote=(ssh -i "$OFM_DEPLOY_SSH_KEY" -o BatchMode=yes "$OFM_DEPLOY_HOST")
  # Always the production compose file on the server.
  prod_compose="cd '$OFM_DEPLOY_DIR' && docker compose -f docker-compose.yml exec -T postgres"
  trap '"${remote[@]}" rm -f /tmp/prod_snapshot.sql || true' EXIT

  echo "== dumping prod =="
  # shellcheck disable=SC2029  # the dir expands here, $POSTGRES_* on the server
  "${remote[@]}" "$prod_compose sh -c 'pg_dump -U \$POSTGRES_USER -d \$POSTGRES_DB --no-owner --no-privileges' > /tmp/prod_snapshot.sql"
  # shellcheck disable=SC2029
  prod_counts=$("${remote[@]}" "$prod_compose psql -U ofmhelpers -d ofmhelpers -At -c \"$counts_sql\"")

  echo "== copying the snapshot =="
  scp -q -i "$OFM_DEPLOY_SSH_KEY" -o BatchMode=yes \
    "$OFM_DEPLOY_HOST:/tmp/prod_snapshot.sql" "$snapshot.part"
  mv "$snapshot.part" "$snapshot"
fi

[ -s "$snapshot" ] || { echo "prod_to_dev: no $snapshot -- run without --reuse" >&2; exit 1; }
echo "snapshot: $snapshot ($(du -h "$snapshot" | cut -f1))"

echo "== stopping dev writers =="
"${compose[@]}" stop "${writers[@]}"
"${compose[@]}" up -d --wait postgres redis

echo "== replacing the dev database =="
"${psql_dev[@]}" -d postgres -q \
  -c "DROP DATABASE IF EXISTS ofmhelpers WITH (FORCE)" \
  -c "CREATE DATABASE ofmhelpers"
"${psql_dev[@]}" -d ofmhelpers -q < "$snapshot" > /dev/null

echo "== starting dev (migrate runs first) =="
"${compose[@]}" up -d
"${compose[@]}" logs --no-log-prefix migrate | tail -5

health=000
for _ in $(seq 30); do
  health=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || true)
  [ "$health" = "200" ] && break
  sleep 2
done
echo "dev /health: $health"

if [ -n "$prod_counts" ]; then
  echo "== row counts that differ (table prod dev) =="
  dev_counts=$("${psql_dev[@]}" -d ofmhelpers -At -c "$counts_sql")
  join -t'|' -a1 -a2 -e0 -o 0,1.2,2.2 \
    <(tr -d '\r' <<< "$prod_counts" | sort) <(tr -d '\r' <<< "$dev_counts" | sort) |
    awk -F'|' '$2 != $3 {print $1, $2, $3; n++} END {if (!n) print "none"}'
fi
[ "$health" = "200" ]
