#!/usr/bin/env bash
# Pull the production backup to this PC (owner's choice 2026-11-20).
# Only two things, per run, into $OFM_BACKUP_DIR (default ~/ofmhelpers-backups,
# outside Documents so no cloud sync picks it up):
#   db/ofmhelpers-<UTC stamp>.sql.gz   pg_dump, verified, newest $OFM_BACKUP_KEEP kept (14)
#   env/env-<UTC stamp>                server .env (holds OFM_VAULT_KEY), checked for the key
# No uploads, no media. Read-only on the server.
# Needs deploy.local.env like scripts/deploy.sh. Daily at 04:00: scripts/schedule_backup.ps1
#
# OFM_BACKUP_LOCAL=1 runs the server half on this machine instead of over ssh
# (dev proof): OFM_DEPLOY_DIR = the checkout, OFM_BACKUP_COMPOSE = the dev pair.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077

if [ "${OFM_BACKUP_LOCAL:-0}" = 1 ]; then
  OFM_DEPLOY_DIR="$PWD"
elif [ -f deploy.local.env ]; then
  set -a
  # shellcheck disable=SC1091
  source deploy.local.env
  set +a
fi

: "${OFM_DEPLOY_DIR:?set OFM_DEPLOY_DIR -- see deploy.local.env.example}"
compose="${OFM_BACKUP_COMPOSE:-docker compose -f docker-compose.yml}"

remote() {
  if [ "${OFM_BACKUP_LOCAL:-0}" = 1 ]; then
    bash -c "$1"
  else
    : "${OFM_DEPLOY_HOST:?set OFM_DEPLOY_HOST -- see deploy.local.env.example}"
    : "${OFM_DEPLOY_SSH_KEY:?set OFM_DEPLOY_SSH_KEY -- see deploy.local.env.example}"
    timeout 3600 ssh -i "$OFM_DEPLOY_SSH_KEY" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=4 "$OFM_DEPLOY_HOST" "$1"
  fi
}

dest="${OFM_BACKUP_DIR:-$HOME/ofmhelpers-backups}"
keep="${OFM_BACKUP_KEEP:-14}"
case "$keep" in
  "" | *[!0-9]* | 0) echo "OFM_BACKUP_KEEP must be a whole number >= 1" >&2; exit 1 ;;
esac
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$dest/db" "$dest/env"
if command -v icacls >/dev/null 2>&1; then
  MSYS_NO_PATHCONV=1 icacls "$(cygpath -w "$dest")" /T /inheritance:r /grant:r "${USERNAME:-$USER}:(OI)(CI)F" >/dev/null
fi

find "$dest/.lock" -maxdepth 0 -mmin +150 -exec rmdir {} + 2>/dev/null || true
if ! mkdir "$dest/.lock" 2>/dev/null; then
  echo "another backup is running (or died: remove $dest/.lock)" >&2
  exit 1
fi
dump="$dest/db/ofmhelpers-$stamp.sql.gz"
envfile="$dest/env/env-$stamp"
cleanup() {
  rm -f "$dump.part" "$envfile.part"
  rmdir "$dest/.lock"
}
trap cleanup EXIT
rm -f "$dest"/db/*.part "$dest"/env/*.part

remote "set -o pipefail; cd '$OFM_DEPLOY_DIR' && $compose exec -T postgres sh -c 'pg_dump -U \$POSTGRES_USER -d \$POSTGRES_DB --no-owner --no-privileges' | gzip" > "$dump.part"
if ! gzip -t "$dump.part" || ! gzip -dc "$dump.part" | tail -c 300 | grep -q 'PostgreSQL database dump complete'; then
  echo "dump incomplete, nothing kept" >&2
  exit 1
fi
mv "$dump.part" "$dump"

remote "cat '$OFM_DEPLOY_DIR/.env'" > "$envfile.part"
if ! grep -q '^OFM_VAULT_KEY=' "$envfile.part"; then
  echo ".env incomplete (no OFM_VAULT_KEY), not kept" >&2
  exit 1
fi
mv "$envfile.part" "$envfile"

prune() {
  printf '%s\n' "$@" | sort -r | tail -n +"$((keep + 1))" | while read -r old; do
    if [ -f "$old" ]; then rm -f -- "$old"; fi
  done
}
prune "$dest"/db/ofmhelpers-*.sql.gz
prune "$dest"/env/env-*

echo "backup ok: $dump ($(du -h "$dump" | cut -f1))"
