# shellcheck shell=sh
# Makes the database user's password the container's POSTGRES_PASSWORD (from
# .env). The postgres image reads POSTGRES_PASSWORD only when its volume is
# first made, so without this a changed value in .env would lock the app out.
# Run by both deploy paths inside the postgres container, over the local
# socket (no password needed), while the app and workers are stopped:
#
#   docker compose -f docker-compose.yml exec -T postgres sh -s < scripts/postgres_password.sh
#
# Idempotent: the same value again changes nothing. The value is hex
# (`openssl rand -hex 32`), so it needs no quoting in SQL.
set -eu
: "${POSTGRES_PASSWORD:?no POSTGRES_PASSWORD in the postgres container}"
printf 'ALTER ROLE "%s" PASSWORD '\''%s'\'';\n' "$POSTGRES_USER" "$POSTGRES_PASSWORD" |
  psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
