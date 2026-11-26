#!/usr/bin/env bash
# Moves production from the server in deploy.local.env to a new one.
#
#   scripts/server_migration.sh ubuntu@<new ip>         copy everything, start it there,
#                                                        old server forwards to it,
#                                                        deploys point at it
#   scripts/server_migration.sh ubuntu@<new ip> finish  after the DNS A records point at it
#
# The new server: a fresh Ubuntu 24.04 VPS that takes the OFM_DEPLOY_SSH_KEY key
# (pick it when ordering) with passwordless sudo, the usual provider default.
# The old server serves while the bulk copy runs. Downtime = the final delta
# copy and the start on the new server (minutes). After that the old server's
# nginx forwards every request to the new one, so the DNS switch costs nothing.
# Jobs still running after OFM_MIGRATION_DRAIN_S (default 900 s), or started in
# the last seconds, are cut off and restarted on the new server by recovery,
# as after a deploy. The Deploy workflow is off while it runs. A failure after
# the old stack stopped stops the new one and starts the old one again.
# Nothing on the old server is deleted.
#
# OFM_MIGRATION_LOCAL=1: a test run between two throwaway local servers: no
# deploy.local.env, no GitHub, no public DNS; OFM_MIGRATION_OLD, OFM_DEPLOY_DIR,
# OFM_DEPLOY_SSH_KEY, OFM_MIGRATION_SSH_CONFIG and OFM_MIGRATION_NEW_IP say where.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

local_run="${OFM_MIGRATION_LOCAL:-0}"
if [ "$local_run" != 1 ] && [ -f deploy.local.env ]; then
  set -a
  # shellcheck disable=SC1091
  source deploy.local.env
  set +a
fi

new_host="${1:?usage: $0 user@new-server [finish]}"
action="${2:-move}"
old_host="${OFM_MIGRATION_OLD:-${OFM_DEPLOY_HOST:?set OFM_DEPLOY_HOST -- see deploy.local.env.example}}"
dir="${OFM_DEPLOY_DIR:?set OFM_DEPLOY_DIR -- see deploy.local.env.example}"
key="${OFM_DEPLOY_SSH_KEY:?set OFM_DEPLOY_SSH_KEY -- see deploy.local.env.example}"
drain_s="${OFM_MIGRATION_DRAIN_S:-900}"
new_user="${new_host%@*}"
new_ip="${OFM_MIGRATION_NEW_IP:-${new_host#*@}}"
[[ "$new_host" =~ ^[a-z_][a-z0-9_-]*@[A-Za-z0-9.:-]+$ ]] || { echo "new server must be user@host, got '$new_host'" >&2; exit 1; }
[[ "$new_ip" =~ ^([0-9.]+|[0-9a-f:]+)$ ]] || { echo "the new server's address must be an IP (or set OFM_MIGRATION_NEW_IP), got '$new_ip'" >&2; exit 1; }
[[ "$dir" =~ ^/[A-Za-z0-9/_.-]+$ ]] || { echo "OFM_DEPLOY_DIR must be a plain absolute path" >&2; exit 1; }
[[ "$drain_s" =~ ^[0-9]+$ ]] || { echo "OFM_MIGRATION_DRAIN_S must be seconds" >&2; exit 1; }

ssh_opts=(-i "$key" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=6)
[ -z "${OFM_MIGRATION_SSH_CONFIG:-}" ] || ssh_opts+=(-F "$OFM_MIGRATION_SSH_CONFIG")
compose="docker compose -f docker-compose.yml"
data_services="postgres redis pot-provider"
site=/etc/nginx/sites-available/ofmhelpers
realip=/etc/nginx/conf.d/ofm-migration-realip.conf
counts_sql="SELECT table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I', table_name), false, true, '')))[1]::text FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1"
psql_counts="$compose exec -T postgres sh -c 'psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -At'"
insecure=""
[ "$local_run" != 1 ] || insecure=-k

on() {
  local host=$1
  shift
  ssh "${ssh_opts[@]}" "$host" "set -euo pipefail; $*"
}
old() { on "$old_host" "$@"; }
new() { on "$new_host" "$@"; }
step() { printf '\n== %s ==\n' "$1"; }
fail() {
  echo "server_migration: $1" >&2
  exit 1
}
github() {
  if [ "$local_run" = 1 ]; then
    echo "local run: gh $* skipped"
  else
    gh "$@"
  fi
}

lock=reports/.server_migration
mkdir -p reports
if ! mkdir "$lock" 2>/dev/null; then
  fail "another migration runs (pid $(cat "$lock/pid" 2>/dev/null || echo unknown); if it died: rm -r $lock)"
fi
echo $$ > "$lock/pid"
old_stopped=0
bridged=0
touched=0
flipped=0
moved=0
keyed=0
deploys_off=0
release_tag=""
on_exit() {
  rc=$?
  trap '' INT TERM HUP
  trap - EXIT
  [ "$keyed" = 0 ] || forget_transfer_key || echo "remove the ofm-migration key by hand: $new_host ~/.ssh/authorized_keys, $old_host $transfer_key" >&2
  if [ "$old_stopped" = 1 ] && [ "$moved" = 0 ]; then
    echo "== the move failed: stopping $new_host, then starting $old_host again ==" >&2
    [ "$bridged" = 0 ] || old "sudo cp $site.before-migration $site && sudo systemctl reload-or-restart nginx" ||
      echo "PUT THE OLD NGINX SITE BACK: ssh $old_host \"sudo cp $site.before-migration $site && sudo systemctl reload nginx\"" >&2
    if new "cd '$dir' && $compose stop >/dev/null && [ -z \"\$($compose ps -q)\" ]"; then
      old "cd '$dir' && OFM_RELEASE='$release_tag' $compose up -d" ||
        echo "START IT BY HAND: ssh $old_host \"cd $dir && OFM_RELEASE=$release_tag $compose up -d\"" >&2
    else
      echo "$new_host could not be confirmed stopped, so $old_host was NOT started (two live copies)." >&2
      echo "Stop the new one: ssh $new_host \"cd $dir && $compose stop\"" >&2
      echo "Then start the old: ssh $old_host \"cd $dir && OFM_RELEASE=$release_tag $compose up -d\"" >&2
    fi
  fi
  if [ "$deploys_off" = 1 ]; then
    if [ "$touched" = 0 ] || { [ "$flipped" = 1 ] && [ "$moved" = 1 ]; }; then
      github workflow enable deploy.yml || echo "turn the Deploy workflow back on: gh workflow enable deploy.yml" >&2
    else
      echo "The Deploy workflow stays OFF: the GitHub secrets OFM_DEPLOY_HOST / OFM_DEPLOY_KNOWN_HOSTS were half changed." >&2
      echo "Set them to the server that runs production, then: gh workflow enable deploy.yml" >&2
    fi
  fi
  rm -r "$lock"
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT TERM HUP

domain=$(old "grep -m1 '^APP_BASE_URL=' '$dir/.env'" | sed -E 's#^APP_BASE_URL=https?://([^/:]+).*#\1#') || fail "no APP_BASE_URL in $old_host:$dir/.env"
[[ "$domain" =~ ^[a-z0-9.-]+$ ]] || fail "APP_BASE_URL in $old_host:$dir/.env names no domain (got '$domain')"

public_records() {
  curl -s --max-time 20 -H 'accept: application/dns-json' "https://cloudflare-dns.com/dns-query?name=$1&type=$2" |
    grep -o "\"type\":$3,\"TTL\":[0-9]*,\"data\":\"[^\"]*\"" | sed -E 's/.*"data":"([^"]*)"/\1/' || true
}

finish() {
  if [ "$local_run" = 1 ]; then
    echo "local run: public DNS not checked"
  else
    step "checking that $domain points at $new_ip only"
    for name in "$domain" "www.$domain"; do
      seen=$(public_records "$name" A 1 | sort -u | tr '\n' ' ')
      [ "$seen" = "$new_ip " ] || fail "$name has A '${seen:-none}': set it to $new_ip only"
      six=$(public_records "$name" AAAA 28)
      [ -z "$six" ] || fail "$name has an AAAA record ($six) that still points at the old server: delete it"
    done
  fi
  step "the new server stops trusting the old one's forwarded addresses"
  new "sudo rm -f $realip && sudo nginx -t && sudo systemctl reload-or-restart nginx"
  status=$(new "curl -s $insecure -o /dev/null -w '%{http_code}' 'https://$domain/health'" || true)
  [ "$status" = 200 ] || fail "https://$domain/health answers $status"
  echo "https://$domain answers from $new_ip."
  echo "The old server $old_host stays stopped with its data: cancel it once the new one has run a few days."
}

if [ "$action" = finish ]; then
  finish
  exit 0
fi
[ "$action" = move ] || fail "unknown action '$action' (move or finish)"

step "checking both servers"
token=$(old 'cat ~/.ofm_migration_id 2>/dev/null || head -c 16 /dev/urandom | od -An -tx1 | tr -d " \n" | tee ~/.ofm_migration_id')
[ -n "$token" ] || fail "cannot write ~/.ofm_migration_id on $old_host"
[ "$(new 'cat ~/.ofm_migration_id 2>/dev/null || true')" != "$token" ] || fail "$new_host is the old server"
running=$(old "cd '$dir' && $compose ps -q ofmhelpers | head -n 1")
[ -n "$running" ] || fail "the app does not run on $old_host (moved already?)"
[ -z "$(new "cd '$dir' 2>/dev/null && $compose ps -q || true")" ] || fail "containers already run on $new_host: stop them first"
release=$(old "docker inspect --format '{{.Config.Image}}' $running")
release_tag="${release##*:}"
project=$(old "docker inspect --format '{{index .Config.Labels \"com.docker.compose.project\"}}' $running")
app_services=$(old "cd '$dir' && $compose config --services" | grep -v -x -E "${data_services// /|}|migrate" | tr '\n' ' ')
docker_version=$(old "docker version --format '{{.Server.Version}}'")
old_home=$(old 'echo "$HOME"')
old_ip=$(old "ip route get '$new_ip' | grep -o 'src [0-9a-f.:]*' | cut -d' ' -f2" || true)
[ -n "$old_ip" ] || fail "$old_host has no route to $new_ip"
old "sudo -n true" || fail "$old_host has no passwordless sudo"
new "sudo -n true" || fail "$new_user on $new_host has no passwordless sudo"
new ". /etc/os-release && [ \"\$ID\" = ubuntu ]" || fail "$new_host is not Ubuntu"
used=$(old "sudo du -sb '$dir' /var/lib/docker | awk '{s += \$1} END {printf \"%d\\n\", s}'")
free=$(new "df -B1 --output=avail / | tail -n 1 | tr -d ' '")
[ "$free" -gt $((used + used / 5 + 10 * 1024 * 1024 * 1024)) ] || fail "$new_host has $((free >> 30)) GB free, needs $(((used + used / 5) >> 30)) + 10 GB"
echo "release $release, Docker $docker_version, at most $((used >> 30)) GB to copy, $domain"
echo "services: $app_services"

step "turning the Deploy workflow off until the move is done"
deploys_off=1
github workflow disable deploy.yml
if [ "$local_run" != 1 ] && [ "$(gh run list --workflow deploy.yml --limit 20 --json status --jq '[.[] | select(.status != "completed")] | length')" != 0 ]; then
  fail "a Deploy run is queued or running: wait for it, then run this again"
fi

step "installing Docker $docker_version, nginx, certbot, ufw and rsync on $new_host"
new "export DEBIAN_FRONTEND=noninteractive
  sudo -E apt-get update -qq
  sudo -E apt-get install -y -qq nginx certbot python3-certbot-nginx ufw rsync curl ca-certificates >/dev/null
  command -v docker >/dev/null || curl -fsSL https://get.docker.com | sudo sh -s -- --version '$docker_version' >/dev/null
  sudo usermod -aG docker \"\$USER\""
old "command -v rsync >/dev/null || sudo apt-get install -y -qq rsync >/dev/null"

step "copying the host setup: nginx + TLS certificates, ssh hardening, BBR, Docker settings, time zone, ssh keys"
new "sudo find /etc/nginx/sites-enabled -mindepth 1 -delete"
old "cd / && sudo tar -czf - etc/nginx etc/letsencrypt \$(ls -d etc/ssh/sshd_config.d/*ofmhelpers* etc/sysctl.d/*ofmhelpers* etc/docker/daemon.json etc/timezone etc/localtime 2>/dev/null || true)" |
  new "sudo tar -xzf - -C /"
old 'cat ~/.ssh/authorized_keys' |
  new 'mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && sort -u -o ~/.ssh/authorized_keys ~/.ssh/authorized_keys - && chmod 600 ~/.ssh/authorized_keys'
new "sudo systemctl restart docker
  sudo sshd -t
  sudo systemctl reload-or-restart ssh
  sudo sysctl --system >/dev/null
  sudo nginx -t
  sudo systemctl reload-or-restart nginx
  for port in 22 80 443; do sudo ufw allow \$port/tcp >/dev/null; done
  sudo ufw --force enable >/dev/null"

transfer_key="$old_home/.ssh/ofm_migration"
ssh_new="ssh -i $transfer_key -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$transfer_key.known_hosts"
to_new="$ssh_new $new_user@$new_ip"
forget_transfer_key() {
  new "sed -i '/ ofm-migration\$/d' ~/.ssh/authorized_keys" &&
    old "rm -f '$transfer_key' '$transfer_key.pub' '$transfer_key.known_hosts'"
}
keyed=1
old "test -f '$transfer_key' || ssh-keygen -q -t ed25519 -N '' -C ofm-migration -f '$transfer_key'"
old "cat '$transfer_key.pub'" |
  new "read -r line && sed -i '/ ofm-migration\$/d' ~/.ssh/authorized_keys && echo \"from=\\\"$old_ip\\\",restrict \$line\" >> ~/.ssh/authorized_keys"
old "$to_new true" || fail "$old_host cannot reach $new_user@$new_ip over ssh from $old_ip (a NAT in between?)"
new "sudo mkdir -p '$dir' && sudo chown \"\$USER:\" '$dir'"
copy_checkout() {
  old "sudo rsync -aH --numeric-ids --delete -e '$ssh_new' --rsync-path='sudo rsync' '$dir/' '$new_user@$new_ip:$dir/'"
}

step "copying the checkout, .env, uploads and downloads (the old server still serves)"
copy_checkout
step "copying the app images (this release and the one kept for a rollback)"
for image in $(old "docker image ls ofmhelpers-app --format '{{.Repository}}:{{.Tag}}'"); do
  if ! new "docker image inspect '$image' >/dev/null 2>&1"; then
    old "docker save '$image' | gzip -1 | $to_new 'gunzip | docker load' >/dev/null"
  fi
  echo "$image"
done
new "cd '$dir' && OFM_RELEASE='$release_tag' $compose pull --quiet $data_services && OFM_RELEASE='$release_tag' $compose create --no-build >/dev/null"

step "waiting up to $drain_s s for running jobs to end"
waited=0
while :; do
  busy=$(old "cd '$dir' && $compose exec -T ofmhelpers python -c 'from ofmhelpers.db.jobs.store import running_jobs; print(\" \".join(f\"{j.task}:{j.id[:8]}\" for j in running_jobs()))'" | tr -d '\r') ||
    fail "cannot read the running jobs on $old_host"
  [ -n "$busy" ] || break
  if [ "$waited" -ge "$drain_s" ]; then
    echo "still running, cut off and restarted on the new server by recovery like after a deploy: $busy"
    break
  fi
  echo "running: $busy"
  sleep 15
  waited=$((waited + 15))
done

step "stopping production on $old_host (downtime starts)"
old_stopped=1
old "cd '$dir' && $compose stop $app_services"
old_counts=$(printf '%s\n' "$counts_sql" | old "cd '$dir' && $psql_counts")
old "cd '$dir' && $compose stop -t 60 $data_services"
old "cd '$dir' && [ \"\$(docker inspect --format '{{.State.ExitCode}}' \$($compose ps -aq postgres))\" = 0 ]" || fail "postgres on $old_host did not shut down cleanly"

step "copying what changed since, and the database, Redis and cookie volumes"
copy_checkout
volumes=$(old "docker volume ls -q --filter label=com.docker.compose.project=$project") || fail "cannot list the volumes on $old_host"
grep -q "postgres_data" <<< "$volumes" || fail "no postgres_data volume on $old_host: '$volumes'"
for volume in $volumes; do
  old "docker run --rm --user 0 --entrypoint tar -v '$volume:/v:ro' '$release' --numeric-owner -cC /v . |
    $to_new \"docker run --rm -i --user 0 --entrypoint sh -v '$volume:/v' '$release' -c 'find /v -mindepth 1 -delete && tar --numeric-owner -xpC /v'\""
  echo "$volume copied"
done

step "starting production on $new_host"
new "cd '$dir' && OFM_RELEASE='$release_tag' $compose up -d --wait postgres redis"
new_counts=$(printf '%s\n' "$counts_sql" | new "cd '$dir' && $psql_counts")
if [ "$old_counts" != "$new_counts" ]; then
  diff <(printf '%s\n' "$old_counts") <(printf '%s\n' "$new_counts") >&2 || true
  fail "row counts differ between the old and the new database"
fi
echo "$(printf '%s\n' "$new_counts" | wc -l) tables, every row count equal"
new "cd '$dir' && OFM_RELEASE='$release_tag' $compose up -d"
status=000
for _ in $(seq 40); do
  status=$(new "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health" || true)
  [ "$status" = 200 ] && break
  sleep 3
done
[ "$status" = 200 ] || fail "/health on $new_host answers $status"
sleep 20
for service in $app_services; do
  state=$(new "cd '$dir' && id=\$($compose ps -q $service | head -n 1) && docker inspect --format '{{.State.Status}} {{.RestartCount}}' \"\$id\"" || true)
  [ "$state" = "running 0" ] || fail "$service on $new_host is '${state:-not there}'"
done
https=$(old "curl -s $insecure -o /dev/null -w '%{http_code}' --resolve '$domain:443:$new_ip' 'https://$domain/health'" || true)
[ "$https" = 200 ] || fail "https://$domain/health on $new_ip answers $https (nginx, certificate or firewall)"

step "the old server forwards every request to the new one (downtime ends)"
new "echo 'set_real_ip_from $old_ip; real_ip_header X-Forwarded-For;' | sudo tee $realip >/dev/null && sudo nginx -t && sudo systemctl reload-or-restart nginx"
old "sudo cp -n $site $site.before-migration"
bridged=1
old "sudo sed -i 's#proxy_pass http://[^/;]*\\([^;]*\\);#proxy_pass https://$new_ip\\1; proxy_ssl_server_name on; proxy_ssl_name \$host;#' $site
  grep -q 'proxy_pass https://$new_ip' $site
  sudo nginx -t
  sudo systemctl reload-or-restart nginx"
forwarded=$(old "curl -s $insecure -o /dev/null -w '%{http_code}' --resolve '$domain:443:127.0.0.1' 'https://$domain/health'" || true)
[ "$forwarded" = 200 ] || fail "the old server's forward to $new_ip answers $forwarded"

step "deploys now go to $new_host"
if [ "$local_run" = 1 ]; then
  echo "local run: deploy.local.env and the GitHub secrets OFM_DEPLOY_HOST / OFM_DEPLOY_KNOWN_HOSTS left as they are"
else
  known=$(old "ssh-keyscan -T 20 '$new_ip' 2>/dev/null")
  [ -n "$known" ] || fail "ssh-keyscan $new_ip from $old_host answered nothing"
  touched=1
  printf '%s\n' "$known" | gh secret set OFM_DEPLOY_KNOWN_HOSTS
  gh secret set OFM_DEPLOY_HOST --body "$new_host"
  sed -i "s#^OFM_DEPLOY_HOST=.*#OFM_DEPLOY_HOST=$new_host#" deploy.local.env
  echo "deploy.local.env and the GitHub secrets OFM_DEPLOY_HOST, OFM_DEPLOY_KNOWN_HOSTS name $new_host"
fi
flipped=1
moved=1

step "the crontab moves"
old 'crontab -l 2>/dev/null || true' | new 'crontab -'
old "crontab -l >/dev/null 2>&1 && crontab -l > ~/crontab.before-migration && crontab -r || true"

step "moved"
echo "Production runs on $new_host ($release): /health 200 over https as $domain, every service up, row counts equal."
echo "$old_host forwards every request to it; its own app is stopped, its data kept, its crontab saved to ~/crontab.before-migration."
echo "Next, whenever you like: set the A records of $domain and www.$domain to $new_ip"
echo "  at your DNS provider, and delete any AAAA record of them,"
echo "then run: scripts/server_migration.sh $new_host finish"
