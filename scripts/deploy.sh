#!/usr/bin/env bash
# Oneshot deploy: commit + push + pull-on-server + rebuild + DB migrate +
# health check.
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

# The server deploys origin/main. Committing on dev and then pushing `main`
# pushed a stale local ref and shipped nothing -- refuse instead.
branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$branch" != "main" ]; then
  echo "On branch '$branch', but this script deploys origin/main." >&2
  echo "Merge into main first (the promote workflow opens the PR), or run" >&2
  echo "the Deploy workflow manually." >&2
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
# Same sequence as .github/workflows/deploy.yml -- keep the two in step.
#
# Order matters, and compose enforces it: the app and worker stop, `migrate`
# runs `alembic upgrade head` to completion, and only then do they start (they
# depend on it with service_completed_successfully). Neither old code on the
# new schema nor new code on the old one ever runs -- the latter 500ed
# /models on 2026-07-27.
remote_script='
  set -eu
  cd "$1"
  # Production file only -- a dev overlay must never apply here.
  compose="docker compose -f docker-compose.yml"
  echo "== fetching =="
  git fetch origin main
  # Safe: production runs the code baked into the image, so the checkout does
  # not touch the running container.
  echo "== checking out =="
  git reset --hard origin/main
  echo "== building =="
  $compose build ofmhelpers worker migrate
  echo "== swapping in the new containers =="
  $compose up -d postgres redis pot-provider
  # Seconds of downtime, so old code never sees the new schema.
  echo "== stopping the app =="
  $compose stop ofmhelpers worker
  # --force-recreate: a deploy that leaves the old containers running while
  # reporting success is the worst outcome. --no-deps so the database is not
  # bounced with them. Compose waits for `migrate` to exit 0 before starting
  # the app and worker.
  echo "== migrating, then starting the app =="
  # A failed migration exits non-zero here (and leaves the app down): print
  # why before `set -e` ends the deploy.
  if ! $compose up -d --no-deps --force-recreate migrate ofmhelpers worker; then
    $compose logs migrate
    exit 1
  fi
  $compose logs migrate
  # Otherwise every deploy leaves its predecessor dangling on the disk.
  docker image prune -f >/dev/null
  # Each build also strands ~1GB of build cache (new .venv layer); cap it,
  # keeping the newest layers so rebuilds stay fast.
  docker builder prune -f --max-used-space 5gb >/dev/null
  echo "== deployed =="
  $compose ps
'
# Delivered as a FILE, not piped into `bash -s`. Piping makes bash read the
# script from stdin as it runs, so the first command that touches stdin --
# `docker compose run` does -- swallows the rest of the script and bash exits 0
# at EOF. That silently skipped `up -d` on 2026-07-27 while reporting success.
ssh -i "$OFM_DEPLOY_SSH_KEY" "$OFM_DEPLOY_HOST" \
  "cat > /tmp/ofm-deploy.sh && bash /tmp/ofm-deploy.sh '$OFM_DEPLOY_DIR'; \
   rc=\$?; rm -f /tmp/ofm-deploy.sh; exit \$rc" <<< "$remote_script"

echo "== health check =="
# The app now starts cold on every deploy, so allow more than the old 5 x 3s.
for i in $(seq 20); do
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
