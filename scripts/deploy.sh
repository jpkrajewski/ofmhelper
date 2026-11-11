#!/usr/bin/env bash
# Manual deploy (the escape hatch; the normal path is merge to main -> CI ->
# .github/workflows/deploy.yml): the server checks out a commit that is
# already on origin/main, builds it (or reuses its kept image), migrates,
# swaps the containers, health check.
#
# Usage: scripts/deploy.sh [sha]
#   (none)  the commit origin/main points at
#   sha     an earlier commit of main (7-40 hex): a rollback. Its image is
#           reused when the server still has it (the one before the current
#           release always is); the migrations must still fit, see README
#           "Deploying".
#
# It never commits and never pushes: what it deploys went through the PR and
# CI like everything else. It refuses a dirty tree and a HEAD that is not
# origin/main, so what you look at is what goes out.
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

if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [sha]" >&2
  exit 1
fi
target="${1:-}"
if [ -n "$target" ] && ! [[ "$target" =~ ^[0-9a-f]{7,40}$ ]]; then
  echo "Not a commit sha (7-40 hex characters): '$target'" >&2
  echo "Usage: $0 [sha]" >&2
  exit 1
fi

# Only what is already on origin/main goes out: no commit, no push from here
# (until 2026-11-11 this script ran `git add -A`, committed and pushed main,
# then deployed with no CI in between).
if [ -n "$(git status --porcelain)" ]; then
  echo "The working tree has uncommitted changes: this script deploys a commit" >&2
  echo "of origin/main, never the working tree. Commit them through a PR" >&2
  echo "(dev -> main), or run it from a clean checkout." >&2
  exit 1
fi
git fetch origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  echo "HEAD ($(git rev-parse --short HEAD)) is not origin/main" \
    "($(git rev-parse --short origin/main))." >&2
  echo "Check out main and pull, or merge the promote PR first: this script" >&2
  echo "deploys only what is already on origin/main." >&2
  exit 1
fi

echo "== deploying ${target:-origin/main} to $OFM_DEPLOY_HOST =="
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
  target="${2:-}"
  # Production file only -- a dev overlay must never apply here.
  compose="docker compose -f docker-compose.yml"
  echo "== fetching =="
  git fetch origin main
  [ -n "$target" ] || target=$(git rev-parse origin/main)
  # Never a commit that is not on main (it did not go through CI).
  if ! git merge-base --is-ancestor "$target" origin/main; then
    echo "$target is not a commit of origin/main: not deployed" >&2
    exit 1
  fi
  echo "deploying $target"
  # The release running now: its image is the one kept for a rollback.
  running=$($compose ps -q ofmhelpers | head -n 1)
  previous=""
  [ -z "$running" ] || previous=$(docker inspect --format "{{.Config.Image}}" "$running")
  # Safe: production runs the code baked into the image, so the checkout does
  # not touch the running container.
  echo "== checking out =="
  git reset --hard "$target"
  # Names the image (ofmhelpers-app:<sha>, docker-compose.yml) and tags every
  # trace with the commit (compose passes it to the app).
  OFM_RELEASE=$(git rev-parse --short HEAD)
  export OFM_RELEASE
  release="ofmhelpers-app:$OFM_RELEASE"
  # A commit whose image is still here (a rollback, a re-deploy) is not built
  # again: the same artifact runs, not a rebuild of it.
  echo "== building $release =="
  docker image inspect "$release" >/dev/null 2>&1 || $compose build ofmhelpers worker sweeps preview-worker media-worker edit-worker migrate
  echo "== swapping in the new containers =="
  # --remove-orphans: a service taken out of the compose file (clone-links-worker,
  # events-worker, 2026-11-11) would run on with the old code, next to the new.
  $compose up -d --remove-orphans postgres redis pot-provider
  # Workers first, while the app still serves: each finishes the job it runs
  # (stop_grace_period, up to 120 s) and takes no new one; jobs queued
  # meanwhile wait in Redis for the new workers.
  # The sweeps with them: nothing queues new work or judges a draining job
  # cut off while the workers stop.
  echo "== stopping the workers =="
  $compose stop sweeps worker preview-worker media-worker edit-worker
  # Seconds of downtime, so old code never sees the new schema.
  echo "== stopping the app =="
  $compose stop ofmhelpers
  # --force-recreate: a deploy that leaves the old containers running while
  # reporting success is the worst outcome. --no-deps so the database is not
  # bounced with them. Compose waits for `migrate` to exit 0 before starting
  # the app and workers.
  echo "== migrating, then starting the app =="
  # A failed migration exits non-zero here (and leaves the app down): print
  # why before `set -e` ends the deploy.
  if ! $compose up -d --no-deps --force-recreate migrate ofmhelpers worker sweeps preview-worker media-worker edit-worker; then
    $compose logs migrate
    exit 1
  fi
  $compose logs migrate
  # Two releases stay on the disk: this one and the one it replaced (what a
  # rollback runs, `scripts/deploy.sh <sha>` or the Deploy workflow with a
  # sha). Older ones go. A re-deploy of the running release replaces nothing,
  # so nothing is removed then. `image prune` takes untagged images only.
  if [ "$previous" != "$release" ]; then
    for image in $(docker image ls ofmhelpers-app --format "{{.Repository}}:{{.Tag}}"); do
      [ "$image" = "$release" ] || [ "$image" = "$previous" ] || docker image rm "$image" >/dev/null || true
    done
  fi
  docker image prune -f >/dev/null
  # Each build also strands ~1GB of build cache (new .venv layer). Only
  # layers used in the last day are kept (the layers of this build, so the next
  # rebuild stays fast): `--max-used-space 5gb` left 8 GB on Docker 29 (prod
  # 2026-11-08, disk at 93%), and a few days of deploys would keep as much.
  docker builder prune -af --filter until=24h >/dev/null
  echo "== deployed =="
  $compose ps
  # The health check below asks the web app only. The sweeps and the workers
  # must be up too: a crash at their start (restart: unless-stopped) would
  # otherwise be a green deploy with recovery, scans and every queue dead.
  sleep 20
  for service in sweeps worker preview-worker media-worker edit-worker; do
    id=$($compose ps -q "$service" | head -n 1)
    state=""
    [ -z "$id" ] || state=$(docker inspect --format "{{.State.Status}} {{.RestartCount}}" "$id")
    if [ "$state" != "running 0" ]; then
      echo "$service is not up after the deploy: ${state:-no container}" >&2
      $compose logs --tail 40 "$service" >&2 || true
      exit 1
    fi
  done
  echo "== sweeps and workers up =="
'
# Delivered as a FILE, not piped into `bash -s`. Piping makes bash read the
# script from stdin as it runs, so the first command that touches stdin --
# `docker compose run` does -- swallows the rest of the script and bash exits 0
# at EOF. That silently skipped `up -d` on 2026-07-27 while reporting success.
ssh -i "$OFM_DEPLOY_SSH_KEY" "$OFM_DEPLOY_HOST" \
  "cat > /tmp/ofm-deploy.sh && bash /tmp/ofm-deploy.sh '$OFM_DEPLOY_DIR' '$target'; \
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
