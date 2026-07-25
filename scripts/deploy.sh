#!/usr/bin/env bash
# Oneshot deploy: commit + push + pull-on-server + rebuild + DB migrate +
# JSON->Postgres backfill + health check.
#
# Usage: scripts/deploy.sh "commit message"
#
# Needs deploy.local.env (gitignored, not committed) next to this repo's root
# defining OFM_DEPLOY_HOST / OFM_DEPLOY_DIR / OFM_DEPLOY_SSH_KEY -- see
# deploy.local.env.example. Kept out of git because this repo is public and
# those values point at the real production server.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f deploy.local.env ]; then
  set -a
  # shellcheck disable=SC1091
  source deploy.local.env
  set +a
fi

: "${OFM_DEPLOY_HOST:?set OFM_DEPLOY_HOST -- see deploy.local.env.example}"
: "${OFM_DEPLOY_DIR:?set OFM_DEPLOY_DIR -- see deploy.local.env.example}"
: "${OFM_DEPLOY_SSH_KEY:?set OFM_DEPLOY_SSH_KEY -- see deploy.local.env.example}"

# Ensure git identity exists (needed for automated commits)
if ! git config user.name >/dev/null || [ -z "$(git config user.name)" ]; then
  git config user.name "${GIT_AUTHOR_NAME:-Jakub}"
fi

if ! git config user.email >/dev/null || [ -z "$(git config user.email)" ] || \
   [ "$(git config user.email)" = "your-email@example.com" ]; then
  git config user.email "${GIT_AUTHOR_EMAIL:-your-real-email@example.com}"
fi

commit_msg="${1:-}"
if [ -z "$commit_msg" ]; then
  echo "Usage: $0 \"commit message\"" >&2
  exit 1
fi

echo "== staging & committing =="
git add -A
if git diff --cached --quiet; then
  echo "Nothing staged -- skipping commit/push, deploying current main as-is."
else
  git commit -m "$commit_msg"
  git push origin main
fi

echo "== deploying to $OFM_DEPLOY_HOST =="
# Bring the stack up (postgres/redis/worker/api), apply DB migrations, then
# backfill any old JSON state into Postgres. The backfill is idempotent
# (upserts by PK) and archives the JSON files after the first run, so it's a
# no-op on every subsequent deploy.
ssh -i "$OFM_DEPLOY_SSH_KEY" "$OFM_DEPLOY_HOST" \
  "cd '$OFM_DEPLOY_DIR' && git pull && docker compose up -d --build && \
   docker compose exec -T ofmhelpers alembic upgrade head && \
   docker compose exec -T ofmhelpers python -m ofmhelpers.web.db.backfill"

echo "== health check =="
for i in 1 2 3 4 5; do
  status=$(ssh -i "$OFM_DEPLOY_SSH_KEY" "$OFM_DEPLOY_HOST" \
    "curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health" 2>/dev/null) || true
  status="${status:-000}"
  if [ "$status" = "200" ]; then
    echo "Health check OK (200)"
    exit 0
  fi
  echo "Health check attempt $i: got $status, retrying..."
  sleep 3
done

echo "Health check FAILED after retries" >&2
exit 1
